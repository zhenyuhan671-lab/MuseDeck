"""MuseDeck · pywebview (Windows WebView2) 原生桌面端桥接层。"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import webview

from . import APP_NAME, DEFAULT_HOME_URL, __version__
from .api_server import LocalApiServer
from .core import cdp, cookies, extensions, proxy_auth, store, window_mgr


def _web_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "musedeck" / "web"
        if p.is_dir():
            return p
    return Path(__file__).resolve().parent / "web"


def _copy_to_clipboard(text: str) -> bool:
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return True
    except Exception:
        pass
    return False


class MuseApi:
    """暴露给前端 WebView JS 调用的 Python 原生 API。"""

    def __init__(self):
        self.cfg = store.load_config()
        self.accounts = store.load_accounts()
        if not self.accounts:
            self.accounts = [{
                "id": store.new_account_id(),
                "name": "Muse 账号 1",
                "browser": "chrome",
                "proxy": "",
                "note": "主力运营号",
                "color": "#FFE100",
                "custom_url": "",
                "group": "默认分组",
                "window_size": "",
                "user_agent": "",
                "login_email": "",
                "login_password": "",
                "vip_expire": "",
                "pinned": False,
                "cdp_port": 9221,
            }]
            store.save_accounts(self.accounts)
        self._procs: dict[str, subprocess.Popen] = {}
        self._launched_at: dict[str, float] = {}
        self._exit_ips: dict[str, str] = {}
        self._last_quota_sync: dict[str, float] = {}
        self._window = None

    def attach_window(self, window):
        self._window = window

    def get_initial_state(self) -> dict:
        return {
            "cfg": self.cfg,
            "accounts": self.accounts,
            "runtime": self.get_runtime_state(),
        }

    def get_runtime_state(self) -> dict:
        out = {}
        now = time.time()
        for acc in self.accounts:
            aid = acc["id"]
            proc = self._procs.get(aid)
            if proc is not None and proc.poll() is None:
                cport = int(acc.get("cdp_port") or 0)
                tab = cdp.inspect_cdp_tabs(cport, timeout=0.35) if cport else {"reachable": False}
                # 自动后台静默同步 Muse 页面额度（每 6 秒节流检查一次，无需用户手点）
                if cport and tab.get("reachable") and (now - self._last_quota_sync.get(aid, 0.0) >= 6.0):
                    self._last_quota_sync[aid] = now
                    threading.Thread(target=self._bg_sync_quota, args=(aid, cport), daemon=True).start()
                out[aid] = {
                    "running": True,
                    "seconds": int(now - self._launched_at.get(aid, now)),
                    "mem_mb": window_mgr.get_process_memory_mb(proc.pid),
                    "cdp_port": cport,
                    "cdp_ok": bool(tab.get("reachable")),
                    "page_title": tab.get("title", ""),
                    "need_login": bool(tab.get("need_login", False)),
                    "needs_attention": bool(tab.get("needs_attention", False)),
                    "attention_label": tab.get("attention_label", ""),
                    "muse_plan": acc.get("muse_plan", "free"),
                    "quota_status": acc.get("quota_status", "ok"),
                    "quota_used_pct": acc.get("quota_used_pct"),
                    "extra_used_pct": acc.get("extra_used_pct"),
                    "quota_reset_day": acc.get("quota_reset_day", ""),
                    "extra_tokens_text": acc.get("extra_tokens_text", ""),
                }
            else:
                self._procs.pop(aid, None)
                self._launched_at.pop(aid, None)
                out[aid] = {
                    "running": False,
                    "seconds": 0,
                    "mem_mb": 0,
                    "cdp_port": int(acc.get("cdp_port") or 0),
                    "cdp_ok": False,
                    "page_title": "",
                    "need_login": False,
                    "needs_attention": False,
                    "attention_label": "",
                    "muse_plan": acc.get("muse_plan", "free"),
                    "quota_status": acc.get("quota_status", "ok"),
                    "quota_used_pct": acc.get("quota_used_pct"),
                    "extra_used_pct": acc.get("extra_used_pct"),
                    "quota_reset_day": acc.get("quota_reset_day", ""),
                    "extra_tokens_text": acc.get("extra_tokens_text", ""),
                }
        return out

    def _save_quota_info(self, acc: dict, info: dict) -> bool:
        changed = False
        for k in ("muse_plan", "quota_used_pct", "extra_used_pct", "quota_status", "quota_reset_day", "extra_tokens_text"):
            val = info.get(k)
            if val is not None and (val != "" or k in ("quota_used_pct", "extra_used_pct")):
                if acc.get(k) != val:
                    acc[k] = val
                    changed = True
        if changed:
            store.save_accounts(self.accounts)
        return changed

    def _bg_sync_quota(self, acc_id: str, cdp_port: int) -> None:
        info = cdp.fetch_muse_quota_via_cdp(cdp_port, timeout=1.0)
        if not info:
            return
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if acc:
            self._save_quota_info(acc, info)

    def sync_muse_quota(self, acc_id: str) -> dict:
        """智能读取目标账号 Muse 双进度条额度：运行中直接 CDP 秒读；未启动则后台无头真实 Chrome 静默读取。"""
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        cport = int(acc.get("cdp_port") or 9221)
        proc = self._procs.get(acc_id)
        if proc is not None and proc.poll() is None:
            info = cdp.fetch_muse_quota_via_cdp(cport, timeout=1.5)
            if not info:
                # 尝试自动展开页内 Settings 弹窗再读一次
                tab = cdp.inspect_cdp_tabs(cport, timeout=0.6)
                if tab.get("reachable") and tab.get("ws"):
                    cdp.cdp_eval_js(tab["ws"], cdp.MUSE_OPEN_SETTINGS_JS, timeout=1.0)
                    time.sleep(1.0)
                    info = cdp.fetch_muse_quota_via_cdp(cport, timeout=1.5)
            if not info:
                return {"ok": False, "message": "未检测到额度条，请确认该窗口已登录 Muse 且开启了 CDP 巡检"}
        else:
            browser = acc.get("browser", "chrome")
            exe = store.find_browser_exe(self.cfg, browser)
            if not exe:
                return {"ok": False, "message": f"未检测到 {browser.upper()} 浏览器"}
            profile = store.profile_dir_for_browser(acc_id, browser)
            eff = store.effective_cfg(self.cfg, acc)
            eff["cdp_enabled"] = True
            start_url = acc.get("custom_url") or self.cfg.get("home_url", DEFAULT_HOME_URL)
            base_args = store.build_chrome_args(eff, profile, start_url=start_url)
            info = cdp.headless_refresh_muse_quota(exe, base_args, cdp_port=cport, timeout=16.0)
            if not info:
                return {"ok": False, "message": "后台静默读取超时（请确认该账号已完成登录且代理可访问 muse.ai）"}

        self._save_quota_info(acc, info)
        pct_str = f"周额度 {info['quota_used_pct']}%" if info.get("quota_used_pct") is not None else info["quota_status"]
        extra = f" · 赠送 {info['extra_used_pct']}%" if info.get("extra_used_pct") is not None else ""
        if info.get("extra_tokens_text"):
            extra += f" ({info['extra_tokens_text']})"
        return {"ok": True, "quota": info, "message": f"已同步「{acc['name']}」：{pct_str}{extra}"}

    def set_theme(self, theme_key: str) -> dict:
        self.cfg["theme"] = store.normalize_theme_key(theme_key)
        store.save_config(self.cfg)
        return {"ok": True}

    def save_account(self, payload: dict) -> dict:
        aid = payload.get("id")
        if aid:
            acc = next((a for a in self.accounts if a["id"] == aid), None)
            if not acc:
                return {"ok": False, "message": "账号不存在"}
            acc.update({
                "name": payload.get("name") or acc["name"],
                "group": payload.get("group") or "默认分组",
                "browser": payload.get("browser") if payload.get("browser") in ("chrome", "edge") else "chrome",
                "vip_expire": payload.get("vip_expire") or "",
                "note": payload.get("note") or "",
                "login_email": payload.get("login_email") or "",
                "login_password": payload.get("login_password") or "",
                "custom_url": payload.get("custom_url") or "",
                "window_size": payload.get("window_size") or "",
                "proxy": payload.get("proxy", ""),
                "color": payload.get("color") or "#FFE100",
                "muse_plan": payload.get("muse_plan") if payload.get("muse_plan") in ("free", "power", "max") else "free",
                "quota_status": payload.get("quota_status") if payload.get("quota_status") in ("ok", "low", "empty") else "ok",
                "quota_reset_day": payload.get("quota_reset_day") or "",
                "agent_role": payload.get("agent_role") or "",
                "agent_prompts": payload.get("agent_prompts") or "",
            })
            msg = f"已更新「{acc['name']}」"
        else:
            name = payload.get("name") or store.next_account_name(self.accounts)
            acc = {
                "id": store.new_account_id(),
                "name": name,
                "group": payload.get("group") or "默认分组",
                "browser": payload.get("browser") if payload.get("browser") in ("chrome", "edge") else "chrome",
                "vip_expire": payload.get("vip_expire") or "",
                "note": payload.get("note") or "",
                "login_email": payload.get("login_email") or "",
                "login_password": payload.get("login_password") or "",
                "custom_url": payload.get("custom_url") or "",
                "window_size": payload.get("window_size") or "",
                "user_agent": "",
                "proxy": payload.get("proxy", ""),
                "color": payload.get("color") or "#FFE100",
                "pinned": bool(payload.get("pinned", False)),
                "cdp_port": store.next_cdp_port(self.accounts),
                "muse_plan": payload.get("muse_plan") if payload.get("muse_plan") in ("free", "power", "max") else "free",
                "quota_status": payload.get("quota_status") if payload.get("quota_status") in ("ok", "low", "empty") else "ok",
                "quota_reset_day": payload.get("quota_reset_day") or "",
                "agent_role": payload.get("agent_role") or "",
                "agent_prompts": payload.get("agent_prompts") or "",
            }
            self.accounts.append(acc)
            seed_id = self.cfg.get("seed_account_id") or ""
            if payload.get("inherit_seed") and seed_id and seed_id != acc["id"]:
                copied = store.clone_seed_profile_clean(seed_id, acc["id"], acc["browser"])
                msg = f"已继承默认模板的 {copied} 项插件/书签并创建「{name}」"
            else:
                msg = f"已创建「{name}」"
        store.save_accounts(self.accounts)
        return {"ok": True, "message": msg}

    def cycle_quota_status(self, acc_id: str) -> dict:
        """一键循环切换账号本周 Muse 额度状态：充裕 -> 偏低 -> 已耗尽。"""
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        order = ("ok", "low", "empty")
        cur = acc.get("quota_status") or "ok"
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "ok"
        acc["quota_status"] = nxt
        store.save_accounts(self.accounts)
        labels = {"ok": "额度充裕", "low": "额度偏低", "empty": "本周已耗尽"}
        return {"ok": True, "quota_status": nxt, "message": f"「{acc['name']}」已标记为：{labels[nxt]}"}

    def open_next_available(self) -> dict:
        """一键启动下一个未运行且本周额度最充裕的 Muse 空闲账号（优先按自动抓取的真实已用百分比升序）。"""
        candidates = [
            a for a in self.accounts
            if (a["id"] not in self._procs or self._procs[a["id"]].poll() is not None)
            and (a.get("quota_status") or "ok") != "empty"
            and (a.get("quota_used_pct") is None or int(a.get("quota_used_pct")) < 99)
        ]
        if not candidates:
            return {"ok": False, "message": "没有可用的空闲满额账号（均在运行中或本周额度已耗尽）"}
        priority = {"ok": 0, "low": 1}
        candidates.sort(
            key=lambda a: (
                int(a["quota_used_pct"]) if a.get("quota_used_pct") is not None else 50,
                priority.get(a.get("quota_status") or "ok", 1),
                not bool(a.get("pinned")),
            )
        )
        target = candidates[0]
        res = self.open_account(target["id"])
        if res.get("ok"):
            pct = f"（已用 {target['quota_used_pct']}%）" if target.get("quota_used_pct") is not None else ""
            res["message"] = f"已智能启动最充裕空闲号「{target['name']}」{pct}"
        return res

    def copy_agent_prompt(self, acc_id: str) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        text = str(acc.get("agent_prompts") or "").strip()
        if not text:
            return {"ok": False, "message": "该账号尚未配置 Soul / 常用派活指令"}
        ok = _copy_to_clipboard(text)
        return {"ok": ok, "message": f"已复制「{acc['name']}」的派活指令，可直接粘贴到 Muse" if ok else "复制失败"}

    def set_seed_account(self, acc_id: str) -> dict:
        cur = self.cfg.get("seed_account_id") or ""
        if cur == acc_id:
            self.cfg["seed_account_id"] = ""
            store.save_config(self.cfg)
            return {"ok": True, "seed_account_id": "", "message": "已取消默认插件模板"}
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        self.cfg["seed_account_id"] = acc_id
        store.save_config(self.cfg)
        return {"ok": True, "seed_account_id": acc_id, "message": f"已将「{acc['name']}」设为默认模板（新建号将自动复制它的插件与书签）"}

    def copy_cdp_endpoint(self, acc_id: str) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        port = int(acc.get("cdp_port") or 0)
        tab = cdp.inspect_cdp_tabs(port, timeout=0.5) if port else {}
        text = tab.get("ws") or f"http://127.0.0.1:{port}"
        ok = _copy_to_clipboard(text)
        return {"ok": ok, "message": f"已复制 Agent/CDP 接管地址: {text}" if ok else "复制失败"}

    def add_unpacked_extension(self) -> dict:
        if not self._window:
            return {"ok": False, "message": "窗口未就绪"}
        path = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not path:
            return {"cancelled": True}
        folder = path[0] if isinstance(path, (list, tuple)) else path
        exts = list(self.cfg.get("unpacked_extensions") or [])
        if folder not in exts:
            exts.append(folder)
            self.cfg["unpacked_extensions"] = exts
            store.save_config(self.cfg)
        return {"ok": True, "unpacked_extensions": exts, "message": f"已挂载未打包扩展目录: {Path(folder).name}"}

    def remove_unpacked_extension(self, index: int) -> dict:
        exts = list(self.cfg.get("unpacked_extensions") or [])
        if 0 <= index < len(exts):
            exts.pop(index)
            self.cfg["unpacked_extensions"] = exts
            store.save_config(self.cfg)
            return {"ok": True, "unpacked_extensions": exts, "message": "已移除未打包扩展目录"}
        return {"ok": False, "message": "索引无效"}

    def set_privacy_pin(self, pin: str) -> dict:
        self.cfg["privacy_pin"] = (pin or "").strip()[:12]
        store.save_config(self.cfg)
        return {"ok": True, "has_pin": bool(self.cfg["privacy_pin"])}

    def verify_privacy_pin(self, pin: str) -> dict:
        saved = str(self.cfg.get("privacy_pin") or "")
        if not saved or saved == (pin or "").strip():
            return {"ok": True}
        return {"ok": False, "message": "PIN 码错误"}

    def toggle_pin(self, acc_id: str) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        acc["pinned"] = not bool(acc.get("pinned", False))
        store.save_accounts(self.accounts)
        return {"ok": True, "pinned": acc["pinned"], "message": f"已{'置顶' if acc['pinned'] else '取消置顶'}「{acc['name']}」"}

    def clone_account(self, acc_id: str) -> dict:
        src = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not src:
            return {"ok": False, "message": "账号不存在"}
        cloned = dict(src)
        cloned["id"] = store.new_account_id()
        cloned["name"] = f"{src['name']} 副本"
        cloned["pinned"] = False
        cloned["cdp_port"] = store.next_cdp_port(self.accounts)
        if isinstance(src.get("proxy"), dict):
            cloned["proxy"] = dict(src["proxy"])
        self.accounts.append(cloned)
        store.save_accounts(self.accounts)
        return {"ok": True, "message": f"已克隆配置并新建「{cloned['name']}」"}

    def import_session(self, acc_id: str) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc or not self._window:
            return {"ok": False, "message": "无法打开文件对话框"}
        if acc_id in self._procs and self._procs[acc_id].poll() is None:
            return {"ok": False, "message": "请先关闭该账号的浏览器窗口再导入会话"}
        path = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=("ZIP 会话包 (*.zip)",),
        )
        if not path:
            return {"cancelled": True}
        src = path[0] if isinstance(path, (list, tuple)) else path
        try:
            res = cookies.import_account_session(acc_id, acc.get("browser", "chrome"), src)
            return {"ok": True, "message": f"已向「{acc['name']}」导入 {res['files']} 个登录会话文件"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def add_bookmark(self, name: str, url: str) -> dict:
        name = (name or "").strip()[:24]
        url = (url or "").strip()
        if not name or not url:
            return {"ok": False, "message": "请输入书签名称与网址"}
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        bms = list(self.cfg.get("bookmarks") or [])
        bms.append({"name": name, "url": url})
        self.cfg["bookmarks"] = bms
        store.save_config(self.cfg)
        return {"ok": True, "bookmarks": bms, "message": f"已添加快捷书签「{name}」"}

    def remove_bookmark(self, index: int) -> dict:
        bms = list(self.cfg.get("bookmarks") or [])
        if 0 <= index < len(bms):
            removed = bms.pop(index)
            self.cfg["bookmarks"] = bms
            store.save_config(self.cfg)
            return {"ok": True, "bookmarks": bms, "message": f"已移除书签「{removed['name']}」"}
        return {"ok": False, "message": "书签不存在"}

    def delete_account(self, acc_id: str) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        self.close_account(acc_id)
        store.delete_profile_dir(acc_id)
        self.accounts = [a for a in self.accounts if a["id"] != acc_id]
        store.save_accounts(self.accounts)
        return {"ok": True, "message": f"已删除「{acc['name']}」"}

    def open_account(self, acc_id: str, extra_urls: list[str] | None = None, force: bool = False) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        # 同 IP 冲突熔断检查
        if not force:
            my_ip = self._exit_ips.get(acc_id)
            if my_ip:
                for other_id, proc in list(self._procs.items()):
                    if other_id != acc_id and proc.poll() is None and self._exit_ips.get(other_id) == my_ip:
                        other_acc = next((a for a in self.accounts if a["id"] == other_id), None)
                        other_name = other_acc["name"] if other_acc else other_id
                        return {
                            "ok": False,
                            "ip_conflict": True,
                            "ip": my_ip,
                            "conflict_name": other_name,
                            "message": f"同 IP 冲突拦截：当前账号与运行中的「{other_name}」出口 IP 均为 {my_ip}",
                        }
        browser = acc.get("browser", "chrome")
        exe = store.find_browser_exe(self.cfg, browser)
        if not exe:
            return {"ok": False, "message": f"未检测到 {browser.upper()}，请在设置中指定路径"}

        profile = store.profile_dir_for_browser(acc_id, browser)
        eff = store.effective_cfg(self.cfg, acc)
        start_url = acc.get("custom_url") or self.cfg.get("home_url", DEFAULT_HOME_URL)
        args = [exe, *store.build_chrome_args(eff, profile, extra_urls=extra_urls, start_url=start_url)]
        try:
            kwargs = {}
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(args, close_fds=True, **kwargs)
            self._procs[acc_id] = proc
            self._launched_at[acc_id] = time.time()
            return {"ok": True, "message": f"已启动「{acc['name']}」"}
        except Exception as exc:
            return {"ok": False, "message": f"启动失败: {exc}"}

    def close_account(self, acc_id: str) -> dict:
        proc = self._procs.pop(acc_id, None)
        self._launched_at.pop(acc_id, None)
        if proc and proc.poll() is None:
            threading.Thread(
                target=window_mgr.close_process_gracefully, args=(proc, 2.0), daemon=True
            ).start()
        return {"ok": True, "message": "已发送安全退出指令"}

    def open_all(self) -> dict:
        unopened = [a["id"] for a in self.accounts if a["id"] not in self._procs or self._procs[a["id"]].poll() is not None]
        if not unopened:
            return {"ok": True, "message": "所有账号均已在运行中"}

        interval = float(self.cfg.get("batch_interval", 1.5))

        def _worker():
            for idx, aid in enumerate(unopened):
                self.open_account(aid)
                if idx + 1 < len(unopened):
                    time.sleep(interval)

        threading.Thread(target=_worker, daemon=True).start()
        return {"ok": True, "message": f"正在按 {interval}s 间隔批量启动 {len(unopened)} 个账号"}

    def close_all(self) -> dict:
        targets = [(aid, p) for aid, p in list(self._procs.items()) if p.poll() is None]
        if not targets:
            return {"ok": True, "message": "当前没有运行中的窗口"}
        for aid, _ in targets:
            self._procs.pop(aid, None)
            self._launched_at.pop(aid, None)

        def _worker():
            for _, p in targets:
                window_mgr.close_process_gracefully(p, 1.8)

        threading.Thread(target=_worker, daemon=True).start()
        return {"ok": True, "message": f"正在异步关闭 {len(targets)} 个窗口"}

    def tile_windows(self) -> dict:
        pids = [p.pid for p in self._procs.values() if p.poll() is None]
        if not pids:
            return {"ok": False, "message": "请先启动至少 1 个账号窗口"}
        placed = window_mgr.tile_windows_for_pids(pids, layout="grid")
        return {"ok": True, "message": f"已平铺 {placed} 个浏览器窗口"}

    def broadcast_url(self, url: str) -> dict:
        running_ids = [aid for aid, p in list(self._procs.items()) if p.poll() is None]
        if not running_ids:
            return {"ok": False, "message": "当前没有运行中的账号窗口"}
        for aid in running_ids:
            acc = next((a for a in self.accounts if a["id"] == aid), None)
            if not acc:
                continue
            browser = acc.get("browser", "chrome")
            exe = store.find_browser_exe(self.cfg, browser)
            if not exe:
                continue
            profile = store.profile_dir_for_browser(aid, browser)
            subprocess.Popen(
                [exe, f"--user-data-dir={profile}", url],
                close_fds=True,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform.startswith("win") else 0,
            )
        return {"ok": True, "message": f"已向 {len(running_ids)} 个运行窗口同步打开链接"}

    def probe_account(self, acc_id: str) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "ms": 0, "detail": "账号不存在"}
        eff = store.effective_cfg(self.cfg, acc)
        res = proxy_auth.probe_account_network(eff, timeout=4.0)
        if res.get("ok") and res.get("ip"):
            self._exit_ips[acc_id] = res["ip"]
        return res

    def copy_credentials(self, acc_id: str) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        parts = []
        if acc.get("login_email"):
            parts.append(acc["login_email"])
        if acc.get("login_password"):
            parts.append(acc["login_password"])
        if not parts:
            return {"ok": False, "message": "该账号未填写账密"}
        ok = _copy_to_clipboard("\n".join(parts))
        return {"ok": ok, "message": "已复制账号与密码到剪贴板" if ok else "复制失败"}

    def clean_cache(self, acc_id: str) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return {"ok": False, "message": "账号不存在"}
        n = store.clean_profile_cache(acc_id, acc.get("browser", "chrome"))
        return {"ok": True, "message": f"已清理 {n} 个缓存目录，登录态已保留"}

    def export_session(self, acc_id: str) -> dict:
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc or not self._window:
            return {"ok": False, "message": "无法打开文件对话框"}
        path = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=f"{acc['name']}-session.zip",
            file_types=("ZIP 会话包 (*.zip)",),
        )
        if not path:
            return {"cancelled": True}
        dest = path[0] if isinstance(path, (list, tuple)) else path
        try:
            res = cookies.export_account_session(acc_id, acc.get("browser", "chrome"), dest)
            return {"ok": True, "message": f"已导出 {res['files']} 个核心会话文件"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def run_health_check(self) -> dict:
        items = []
        chrome = store.find_chrome(self.cfg)
        items.append({"title": "Google Chrome", "status": "ok" if chrome else "warn", "detail": chrome or "未检测到"})
        edge = store.find_edge(self.cfg)
        items.append({"title": "Microsoft Edge", "status": "ok" if edge else "warn", "detail": edge or "未检测到"})
        sp = store.system_proxy_url()
        items.append({"title": "系统代理", "status": "ok" if sp else "warn", "detail": sp or "未开启（TUN 可忽略）"})

        mode = self.cfg.get("proxy_mode", "local")
        if mode == "local":
            pt, h, p = self.cfg["proxy_type"], self.cfg["proxy_host"], int(self.cfg["proxy_port"])
            ok, detail, ms = proxy_auth.timed_probe(pt, h, p, timeout=3.5)
            items.append({
                "title": "本地代理连通性",
                "status": "ok" if ok else "fail",
                "detail": f"{pt.upper()} {h}:{p} 可用 · {ms} ms" if ok else f"不可达: {detail}",
            })
        else:
            items.append({"title": "代理模式", "status": "ok", "detail": "跟随系统" if mode == "system" else "直连 / TUN"})

        ports = []
        for p in store.COMMON_PROXY_PORTS:
            ok, _ = proxy_auth.probe_http_proxy("127.0.0.1", p, timeout=0.22)
            if not ok:
                ok, _ = proxy_auth.probe_socks5_proxy("127.0.0.1", p, timeout=0.22)
            if ok:
                ports.append(p)
        return {"items": items, "ports": ports}

    def apply_port(self, port: int) -> dict:
        self.cfg["proxy_mode"] = "local"
        self.cfg["proxy_port"] = int(port)
        store.save_config(self.cfg)
        return {"ok": True, "message": f"已将全局代理端口设为 {port}"}

    def save_settings(self, data: dict) -> dict:
        self.cfg.update(data)
        store.save_config(self.cfg)
        return {"ok": True, "message": "全局设置已保存"}

    def export_backup(self, include_profiles: bool) -> dict:
        if not self._window:
            return {"ok": False, "message": "窗口未就绪"}
        tag = "full" if include_profiles else "light"
        path = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=f"MuseDeck-Backup-{tag}.zip",
            file_types=("ZIP 备份 (*.zip)",),
        )
        if not path:
            return {"cancelled": True}
        dest = path[0] if isinstance(path, (list, tuple)) else path
        try:
            store.export_backup(dest, include_profiles)
            return {"ok": True, "message": "备份导出成功"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def import_backup(self) -> dict:
        if not self._window:
            return {"ok": False, "message": "窗口未就绪"}
        path = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=("ZIP 备份 (*.zip)",),
        )
        if not path:
            return {"cancelled": True}
        src = path[0] if isinstance(path, (list, tuple)) else path
        try:
            res = store.import_backup(src)
            self.cfg = store.load_config()
            self.accounts = store.load_accounts()
            return {"ok": True, "message": f"已恢复 {res['accounts']} 个账号"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def scan_plugins(self) -> list[dict]:
        return extensions.list_source_extensions()

    def install_plugins(self, acc_id: str, ext_ids: list[str]) -> dict:
        urls = [extensions.store_url(eid) for eid in ext_ids]
        return self.open_account(acc_id, extra_urls=urls)

    def open_external(self, url: str) -> None:
        webbrowser.open(url)


def run() -> int:
    api = MuseApi()
    http_srv = None
    if api.cfg.get("api_enabled", True):
        http_srv = LocalApiServer(api, port=int(api.cfg.get("api_port", 18900)))
        http_srv.start()

    index_html = (_web_root() / "index.html").resolve()
    window = webview.create_window(
        title=f"{APP_NAME} v{__version__}",
        url=index_html.as_uri(),
        js_api=api,
        width=1220,
        height=780,
        min_size=(940, 620),
        background_color="#0b0d11",
    )
    api.attach_window(window)
    try:
        webview.start(gui="edgechromium")
    finally:
        if http_srv is not None:
            http_srv.stop()
    return 0
