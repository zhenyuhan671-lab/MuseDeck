"""Chromium DevTools Protocol (CDP) 轻量本地巡检与 Muse 额度自动抓取引擎（纯标准库，零外部依赖）。"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request

LOGIN_URL_KEYWORDS = ("/login", "/signin", "/sign-in", "/auth", "accounts.google.com", "oauth")
LOGIN_TITLE_KEYWORDS = ("sign in", "log in", "登录", "登入")
APPROVAL_KEYWORDS = ("approval", "approve", "action required", "permission", "review", "待审批", "需要确认", "等待确认")
UNREAD_TITLE_RE = re.compile(r"^\s*[\(\[]\s*(\d+)\s*[\)\]]")

# 直接在 Muse 页面执行的 DOM 额度提取脚本（精准匹配 role="progressbar" 及相邻说明文字）
MUSE_QUOTA_JS = r"""
(() => {
  const bars = Array.from(document.querySelectorAll('[role="progressbar"]'));
  if (!bars.length) return null;
  const items = bars.map((bar) => {
    const label = (bar.getAttribute('aria-label') || '').trim();
    const valNow = Number(bar.getAttribute('aria-valuenow'));
    const container = bar.closest('.flex.flex-col.gap-2') || bar.parentElement;
    const text = container ? (container.innerText || '').trim() : '';
    return {
      label,
      value: Number.isFinite(valNow) ? valNow : null,
      text
    };
  });
  return { items };
})()
"""


def _ws_send_text(sock: socket.socket, text: str) -> None:
    data = text.encode("utf-8")
    mask_key = os.urandom(4)
    header = bytearray([0x81])
    length = len(data)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    header.extend(mask_key)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + masked)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("WebSocket closed")
        buf.extend(chunk)
    return bytes(buf)


def _ws_recv_text(sock: socket.socket) -> str:
    while True:
        b1, b2 = _recv_exact(sock, 2)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
        mask_key = _recv_exact(sock, 4) if masked else b""
        payload = _recv_exact(sock, length)
        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            raise ConnectionError("WebSocket close frame")
        if opcode == 0x9:  # ping -> pong
            continue
        if opcode in (0x1, 0x0):
            return payload.decode("utf-8", errors="replace")


def cdp_eval_js(ws_url: str, expression: str, timeout: float = 1.5):
    """通过本地 CDP WebSocket 在目标标签页执行 JS 并返回反序列化结果。"""
    if not ws_url or not ws_url.startswith("ws://"):
        return None
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 9222
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(req.encode("ascii"))
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = sock.recv(1024)
                if not chunk:
                    return None
                resp += chunk
            if b" 101 " not in resp.split(b"\r\n", 1)[0]:
                return None

            cmd = {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True},
            }
            _ws_send_text(sock, json.dumps(cmd))
            for _ in range(8):
                raw = _ws_recv_text(sock)
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    return ((msg.get("result") or {}).get("result") or {}).get("value")
    except Exception:
        return None
    return None


# 自动在 Muse 页面尝试点击左下角菜单/Settings 按钮以展开额度弹窗
MUSE_OPEN_SETTINGS_JS = r"""
(() => {
  if (document.querySelector('[role="progressbar"]')) return 'already_open';
  const clickMatch = (preds) => {
    const els = Array.from(document.querySelectorAll('button, [role="button"], [role="menuitem"], a'));
    for (const el of els) {
      const txt = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')).toLowerCase();
      if (preds.some((p) => txt.includes(p))) {
        el.click();
        return true;
      }
    }
    return false;
  };
  if (clickMatch(['settings', '设置'])) return 'clicked_settings';
  // 若 Settings 在左下角菜单(=)内，先点左下角菜单按钮
  const btns = Array.from(document.querySelectorAll('button')).filter((b) => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.left < 90 && r.bottom > window.innerHeight - 100;
  });
  if (btns.length) {
    btns[btns.length - 1].click();
    return 'clicked_corner_menu';
  }
  return 'not_found';
})()
"""


def parse_muse_quota_items(items: list[dict]) -> dict | None:
    """将从 Muse DOM 抓取到的 progressbar 列表解析为周额度 + 赠送额度双进度条对象。"""
    if not items:
        return None
    plan = ""
    used_pct: int | None = None
    extra_used_pct: int | None = None
    reset_text = ""
    extra_text = ""

    for it in items:
        label = str(it.get("label") or "").strip()
        low_label = label.lower()
        val = it.get("value")
        text = str(it.get("text") or "")

        if "plan" in low_label or low_label in ("free", "power", "maximum"):
            if "max" in low_label:
                plan = "max"
            elif "power" in low_label:
                plan = "power"
            else:
                plan = "free"
            if isinstance(val, (int, float)):
                used_pct = max(0, min(100, int(round(val))))
            m_reset = re.search(r"(?:resets?\s+on\s+|重置[于在]?\s*)([A-Za-z0-9\s\-\u4e00-\u9fff]{2,20})", text, re.I)
            if m_reset:
                reset_text = m_reset.group(1).strip().splitlines()[0]
        elif "additional" in low_label or "extra" in low_label or "token" in low_label:
            if isinstance(val, (int, float)):
                extra_used_pct = max(0, min(100, int(round(val))))
            m_left = re.search(r"\(([^)]*tokens?\s+left[^)]*)\)", text, re.I)
            if m_left:
                extra_text = m_left.group(1).strip()
            elif extra_used_pct is not None:
                extra_text = f"已用 {extra_used_pct}%"

    if used_pct is None and extra_used_pct is None and not extra_text:
        return None

    status = "ok"
    if used_pct is not None:
        if used_pct >= 99:
            status = "empty"
        elif used_pct >= 75:
            status = "low"

    return {
        "muse_plan": plan or "free",
        "quota_used_pct": used_pct,
        "extra_used_pct": extra_used_pct,
        "quota_status": status,
        "quota_reset_day": reset_text,
        "extra_tokens_text": extra_text,
    }


def headless_refresh_muse_quota(
    exe_path: str,
    base_args: list[str],
    cdp_port: int,
    timeout: float = 16.0,
) -> dict | None:
    """使用真实 Chrome --headless=new 后台静默打开 muse.ai，自动点开 Settings 读取双进度条后退出。"""
    args = [exe_path, "--headless=new", "--window-size=1280,860"]
    for a in base_args:
        if not a.startswith("--remote-debugging-port="):
            args.append(a)
    args.append(f"--remote-debugging-port={int(cdp_port)}")

    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            info = fetch_muse_quota_via_cdp(cdp_port, timeout=1.0)
            if info:
                return info
            # 尝试在页面里点击左下角菜单 / Settings 展开额度弹窗
            tab = inspect_cdp_tabs(cdp_port, timeout=0.6)
            if tab.get("reachable") and tab.get("ws"):
                cdp_eval_js(tab["ws"], MUSE_OPEN_SETTINGS_JS, timeout=1.0)
            time.sleep(1.1)
        return None
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def fetch_muse_quota_via_cdp(port: int, timeout: float = 1.2) -> dict | None:
    """自动扫描指定 CDP 端口下所有 Muse 标签页并提取实时额度。"""
    if not port or port <= 0:
        return None
    url = f"http://127.0.0.1:{int(port)}/json/list"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as resp:
            tabs = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None

    pages = [t for t in tabs if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    # 优先检查 URL 含 muse 的页面
    pages.sort(key=lambda t: 0 if "muse" in str(t.get("url", "")).lower() else 1)
    for p in pages:
        raw = cdp_eval_js(str(p["webSocketDebuggerUrl"]), MUSE_QUOTA_JS, timeout=timeout)
        if isinstance(raw, dict) and raw.get("items"):
            parsed = parse_muse_quota_items(raw["items"])
            if parsed:
                return parsed
    return None


def inspect_cdp_tabs(port: int, timeout: float = 0.8) -> dict:
    """查询本地 CDP 端口的活跃标签页，返回标题、URL、WS 地址、掉登录与待审批告警。"""
    if not port or port <= 0:
        return {"reachable": False}
    url = f"http://127.0.0.1:{int(port)}/json/list"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as resp:
            tabs = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return {"reachable": False}

    pages = [t for t in tabs if isinstance(t, dict) and t.get("type") == "page"]
    if not pages:
        return {
            "reachable": True,
            "title": "",
            "url": "",
            "ws": "",
            "need_login": False,
            "needs_attention": False,
            "attention_label": "",
            "tab_count": 0,
        }

    active = next(
        (p for p in pages if not str(p.get("url", "")).startswith(("chrome://", "edge://", "devtools://"))),
        pages[0],
    )
    page_url = str(active.get("url") or "")
    page_title = str(active.get("title") or "").strip()
    ws_url = str(active.get("webSocketDebuggerUrl") or "")

    low_url = page_url.lower()
    low_title = page_title.lower()
    need_login = any(k in low_url for k in LOGIN_URL_KEYWORDS) or any(k in low_title for k in LOGIN_TITLE_KEYWORDS)

    needs_attention = False
    attention_label = ""
    if not need_login:
        if any(k in low_title or k in low_url for k in APPROVAL_KEYWORDS):
            needs_attention = True
            attention_label = "待审批"
        elif UNREAD_TITLE_RE.search(page_title):
            m = UNREAD_TITLE_RE.search(page_title)
            needs_attention = True
            attention_label = f"新动态({m.group(1)})"

    return {
        "reachable": True,
        "title": page_title[:60],
        "url": page_url,
        "ws": ws_url,
        "endpoint": f"http://127.0.0.1:{int(port)}",
        "need_login": need_login,
        "needs_attention": needs_attention,
        "attention_label": attention_label,
        "tab_count": len(pages),
    }
