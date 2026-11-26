"""代理鉴权扩展生成、延迟测速与公网出口 IP 探测。"""

from __future__ import annotations

import base64
import json
import socket
import ssl
import time
import urllib.request
from pathlib import Path


def ensure_proxy_auth_extension(
    profile_dir: Path,
    host: str,
    port: int,
    username: str,
    password: str,
    scheme: str = "http",
) -> Path:
    """在指定账号 Profile 内生成轻量 Chrome Proxy Auth 扩展，自动完成账密认证。"""
    ext_dir = Path(profile_dir) / "_muse_proxy_auth_ext"
    ext_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "manifest_version": 2,
        "name": "MuseDeck Proxy Auth Helper",
        "version": "1.0.0",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0",
    }

    safe_scheme = "socks5" if scheme == "socks5" else "http"
    config_js = json.dumps(
        {
            "mode": "fixed_servers",
            "rules": {
                "singleProxy": {
                    "scheme": safe_scheme,
                    "host": host,
                    "port": int(port),
                },
                "bypassList": ["localhost", "127.0.0.1"],
            },
        },
        ensure_ascii=False,
    )
    user_js = json.dumps(username, ensure_ascii=False)
    pass_js = json.dumps(password, ensure_ascii=False)

    background_js = (
        f"var config = {config_js};\n"
        "chrome.proxy.settings.set({value: config, scope: 'regular'}, function() {});\n"
        "function callbackFn(details) {\n"
        f"  return {{ authCredentials: {{ username: {user_js}, password: {pass_js} }} }};\n"
        "}\n"
        "chrome.webRequest.onAuthRequired.addListener(\n"
        "  callbackFn,\n"
        "  {urls: ['<all_urls>']},\n"
        "  ['blocking']\n"
        ");\n"
    )

    (ext_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ext_dir / "background.js").write_text(background_js, encoding="utf-8")
    return ext_dir


def probe_http_proxy(
    host: str,
    port: int,
    timeout: float = 2.5,
    username: str = "",
    password: str = "",
) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            s.settimeout(timeout)
            auth_hdr = ""
            if username:
                token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
                auth_hdr = f"Proxy-Authorization: Basic {token}\r\n"
            req = (
                "GET http://www.gstatic.com/generate_204 HTTP/1.1\r\n"
                "Host: www.gstatic.com\r\n"
                f"{auth_hdr}"
                "Connection: close\r\n\r\n"
            )
            s.sendall(req.encode("latin-1"))
            raw = s.recv(256).decode("latin-1", errors="replace")
            line = raw.splitlines()[0] if raw else ""
        if " 204" in line or " 200" in line:
            return True, line.strip()
        if " 407" in line:
            return False, "代理需要账密认证 (HTTP 407)"
        return False, f"响应状态异常: {line.strip()[:60]}"
    except Exception as exc:
        return False, str(exc)[:60]


def probe_socks5_proxy(
    host: str,
    port: int,
    timeout: float = 2.5,
    username: str = "",
    password: str = "",
) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            s.settimeout(timeout)
            if username:
                s.sendall(b"\x05\x02\x00\x02")
                resp = s.recv(2)
                if len(resp) < 2 or resp[0] != 5:
                    return False, "SOCKS5 握手失败"
                if resp[1] == 2:
                    u = username.encode("utf-8")
                    p = password.encode("utf-8")
                    s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
                    auth_resp = s.recv(2)
                    if len(auth_resp) < 2 or auth_resp[1] != 0:
                        return False, "SOCKS5 账密认证失败"
                elif resp[1] != 0:
                    return False, "SOCKS5 握手被拒"
            else:
                s.sendall(b"\x05\x01\x00")
                if s.recv(2) != b"\x05\x00":
                    return False, "SOCKS5 握手被拒"
            target = b"www.gstatic.com"
            s.sendall(
                b"\x05\x01\x00\x03"
                + bytes([len(target)])
                + target
                + (80).to_bytes(2, "big")
            )
            reply = s.recv(10)
            if len(reply) < 2 or reply[1] != 0:
                return False, "SOCKS5 目标建连失败"
            s.sendall(
                b"GET /generate_204 HTTP/1.1\r\n"
                b"Host: www.gstatic.com\r\nConnection: close\r\n\r\n"
            )
            raw = s.recv(256).decode("latin-1", errors="replace")
            line = raw.splitlines()[0] if raw else ""
        if " 204" in line or " 200" in line:
            return True, line.strip()
        return False, f"响应状态异常: {line.strip()[:60]}"
    except Exception as exc:
        return False, str(exc)[:60]


def probe_proxy(
    ptype: str,
    host: str,
    port: int,
    timeout: float = 3.5,
    username: str = "",
    password: str = "",
) -> tuple[bool, str]:
    if ptype == "socks5":
        return probe_socks5_proxy(host, port, timeout=timeout, username=username, password=password)
    return probe_http_proxy(host, port, timeout=timeout, username=username, password=password)


def timed_probe(
    ptype: str,
    host: str,
    port: int,
    timeout: float = 4.0,
    username: str = "",
    password: str = "",
) -> tuple[bool, str, int]:
    t0 = time.monotonic()
    ok, detail = probe_proxy(ptype, host, port, timeout=timeout, username=username, password=password)
    return ok, detail, int((time.monotonic() - t0) * 1000)


def fetch_exit_ip_geo(
    proxy_url: str | None = None,
    timeout: float = 4.0,
) -> dict:
    """查询出口 IP 与国家代码，失败返回空字段，不抛异常。"""
    handlers = []
    if proxy_url and proxy_url.startswith(("http://", "https://")):
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    elif proxy_url is None:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(
        "http://ip-api.com/json/?fields=status,countryCode,query",
        headers={"User-Agent": "MuseDeck/1.0"},
    )
    try:
        ctx = ssl._create_unverified_context()
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if data.get("status") == "success":
                return {
                    "ip": str(data.get("query") or ""),
                    "country": str(data.get("countryCode") or ""),
                }
    except Exception:
        pass
    return {"ip": "", "country": ""}


def probe_account_network(eff_cfg: dict, timeout: float = 4.0) -> dict:
    """对账号生效的代理配置执行测速与出口归属地探测。"""
    mode = eff_cfg.get("proxy_mode", "local")
    if mode == "local" and eff_cfg.get("proxy_enabled", True):
        ptype = eff_cfg.get("proxy_type", "http")
        host = eff_cfg.get("proxy_host", "127.0.0.1")
        port = int(eff_cfg.get("proxy_port", 7892))
        user = str(eff_cfg.get("proxy_username") or "")
        pwd = str(eff_cfg.get("proxy_password") or "")
        ok, detail, ms = timed_probe(ptype, host, port, timeout=timeout, username=user, password=pwd)
        geo = {"ip": "", "country": ""}
        if ok and ptype == "http":
            auth_part = f"{user}:{pwd}@" if user else ""
            geo = fetch_exit_ip_geo(f"http://{auth_part}{host}:{port}", timeout=min(timeout, 3.5))
        return {"ok": ok, "ms": ms, "detail": detail, "ip": geo["ip"], "country": geo["country"]}

    # 直连或系统代理：直接测出口
    t0 = time.monotonic()
    geo = fetch_exit_ip_geo(None if mode == "off" else "", timeout=timeout)
    ms = int((time.monotonic() - t0) * 1000)
    ok = bool(geo["ip"])
    return {
        "ok": ok,
        "ms": ms,
        "detail": "直连可达" if ok else "直连检测超时",
        "ip": geo["ip"],
        "country": geo["country"],
    }
