"""配置、账号与浏览器启动逻辑（MuseDeck 核心持久化与参数引擎）。"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

from .. import DEFAULT_HOME_URL
from .extensions import list_source_extensions, main_chrome_user_data, store_url
from .proxy_auth import ensure_proxy_auth_extension

DATA_DIR_NAME = "DeckData"

DEFAULT_CONFIG = {
    "proxy_enabled": True,
    "proxy_mode": "local",  # local 本地代理 / system 跟随系统代理 / off 直连（TUN）
    "proxy_type": "http",  # http | socks5
    "proxy_host": "127.0.0.1",
    "proxy_port": 7892,
    "home_url": DEFAULT_HOME_URL,
    "chrome_path": "",
    "edge_path": "",
    "lang_en": True,
    "webrtc_protect": True,
    "anti_fingerprint": True,
    "theme": "dark",  # dark / light
    "backdrop_mode": "acrylic",
    "view_mode": "grid",  # grid / table
    "minimize_to_tray": True,
    "cdp_enabled": True,  # 关闭后启动原生 Chrome 不带调试端口（100% 纯净模式）
    "api_enabled": True,
    "api_port": 18900,
    "win_geo": None,
    "batch_interval": 1.5,
    "privacy_pin": "",
    "unpacked_extensions": [],
    "seed_account_id": "",
    "bookmarks": [
        {"name": "Muse.ai", "url": "https://muse.ai"},
        {"name": "IP 检测", "url": "https://ipinfo.io"},
        {"name": "Google", "url": "https://www.google.com"},
    ],
}

COMMON_PROXY_PORTS = [
    7892, 7890, 7897, 7893, 7895, 10808, 10809,
    1080, 1081, 6152, 6153, 20171, 2080, 8080, 8118, 8888, 7070,
]

PROXY_PRESETS = [
    ("Clash Verge / Mihomo", "http", "127.0.0.1", 7892),
    ("Clash / Meta", "http", "127.0.0.1", 7890),
    ("v2rayN", "http", "127.0.0.1", 10808),
    ("Shadowsocks", "socks5", "127.0.0.1", 1080),
]

WINDOW_SIZE_PRESETS = [
    ("跟随浏览器默认", ""),
    ("1440 × 900 (推荐桌面)", "1440,900"),
    ("1280 × 800 (标准宽屏)", "1280,800"),
    ("1600 × 960 (大屏运营)", "1600,960"),
    ("1080 × 760 (紧凑平铺)", "1080,760"),
]


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def data_dir() -> Path:
    d = base_dir() / DATA_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def profiles_dir() -> Path:
    d = data_dir() / "chrome-profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_dir_for(acc_id: str) -> Path:
    d = profiles_dir() / acc_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def edge_profiles_dir() -> Path:
    d = data_dir() / "edge-profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def edge_profile_dir_for(acc_id: str) -> Path:
    d = edge_profiles_dir() / acc_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_dir_for_browser(acc_id: str, browser: str) -> Path:
    if browser == "edge":
        return edge_profile_dir_for(acc_id)
    return profile_dir_for(acc_id)


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def normalize_theme_key(raw: str | None) -> str:
    val = str(raw or "").strip().lower()
    if "light" in val:
        return "light"
    return "dark"


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    saved = _read_json(data_dir() / "config.json", {})
    if isinstance(saved, dict):
        cfg.update({k: saved[k] for k in DEFAULT_CONFIG if k in saved})
    try:
        cfg["proxy_port"] = int(cfg["proxy_port"])
        if not (1 <= cfg["proxy_port"] <= 65535):
            raise ValueError
    except Exception:
        cfg["proxy_port"] = DEFAULT_CONFIG["proxy_port"]
    if cfg["proxy_type"] not in ("http", "socks5"):
        cfg["proxy_type"] = "http"
    if not str(cfg["home_url"]).startswith(("http://", "https://")):
        cfg["home_url"] = DEFAULT_HOME_URL
    for key in ("proxy_enabled", "lang_en", "webrtc_protect", "anti_fingerprint", "minimize_to_tray"):
        cfg[key] = bool(cfg.get(key, DEFAULT_CONFIG[key]))
    if isinstance(saved, dict) and saved.get("proxy_mode") in ("local", "system", "off"):
        cfg["proxy_mode"] = saved["proxy_mode"]
    else:
        cfg["proxy_mode"] = "local" if cfg["proxy_enabled"] else "off"
    cfg["proxy_host"] = str(cfg["proxy_host"]).strip() or "127.0.0.1"
    cfg["chrome_path"] = str(cfg.get("chrome_path", "")).strip()
    cfg["edge_path"] = str(cfg.get("edge_path", "")).strip()
    cfg["theme"] = normalize_theme_key(cfg.get("theme"))
    if cfg.get("backdrop_mode") not in ("acrylic", "mica", "solid"):
        cfg["backdrop_mode"] = "acrylic"
    if cfg.get("view_mode") not in ("grid", "compact"):
        cfg["view_mode"] = "grid"
    return cfg


def save_config(cfg: dict) -> None:
    _write_json(data_dir() / "config.json", cfg)


def load_accounts() -> list[dict]:
    saved = _read_json(data_dir() / "accounts.json", [])
    accounts = []
    if isinstance(saved, list):
        for item in saved:
            if isinstance(item, dict) and item.get("id") and item.get("name"):
                browser = str(item.get("browser") or "chrome").lower()
                if browser not in ("chrome", "edge"):
                    browser = "chrome"
                proxy = item.get("proxy") or ""
                if isinstance(proxy, dict):
                    try:
                        port = int(proxy.get("port"))
                        if not (1 <= port <= 65535):
                            raise ValueError
                        p_obj = {
                            "type": proxy.get("type") if proxy.get("type") in ("http", "socks5") else "http",
                            "host": str(proxy.get("host") or "127.0.0.1").strip() or "127.0.0.1",
                            "port": port,
                        }
                        if proxy.get("username"):
                            p_obj["username"] = str(proxy.get("username")).strip()
                        if proxy.get("password"):
                            p_obj["password"] = str(proxy.get("password"))
                        proxy = p_obj
                    except Exception:
                        proxy = ""
                elif proxy != "direct":
                    proxy = ""
                note = str(item.get("note") or "")[:100]
                color = str(item.get("color") or "")
                if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                    color = ""
                custom_url = str(item.get("custom_url") or "").strip()
                if custom_url and not custom_url.startswith(("http://", "https://")):
                    custom_url = ""
                group = str(item.get("group") or "默认分组").strip() or "默认分组"
                window_size = str(item.get("window_size") or "").strip()
                if window_size and not re.fullmatch(r"\d{3,4},\d{3,4}", window_size):
                    window_size = ""
                user_agent = str(item.get("user_agent") or "").strip()[:240]
                login_email = str(item.get("login_email") or "").strip()[:120]
                login_password = str(item.get("login_password") or "")[:120]
                vip_expire = str(item.get("vip_expire") or "").strip()
                if vip_expire and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", vip_expire):
                    vip_expire = ""
                pinned = bool(item.get("pinned", False))
                muse_plan = str(item.get("muse_plan") or "free").lower()
                if muse_plan not in ("free", "power", "max"):
                    muse_plan = "free"
                quota_status = str(item.get("quota_status") or "ok").lower()
                if quota_status not in ("ok", "low", "empty"):
                    quota_status = "ok"
                quota_reset_day = str(item.get("quota_reset_day") or "").strip()[:24]
                raw_pct = item.get("quota_used_pct")
                quota_used_pct = None
                if raw_pct is not None and raw_pct != "":
                    try:
                        quota_used_pct = max(0, min(100, int(raw_pct)))
                    except Exception:
                        quota_used_pct = None
                raw_extra_pct = item.get("extra_used_pct")
                extra_used_pct = None
                if raw_extra_pct is not None and raw_extra_pct != "":
                    try:
                        extra_used_pct = max(0, min(100, int(raw_extra_pct)))
                    except Exception:
                        extra_used_pct = None
                extra_tokens_text = str(item.get("extra_tokens_text") or "").strip()[:48]
                agent_role = str(item.get("agent_role") or "").strip()[:40]
                agent_prompts = str(item.get("agent_prompts") or "")[:2000]
                cdp_port = 0
                try:
                    raw_port = int(item.get("cdp_port") or 0)
                    if 1024 <= raw_port <= 65535:
                        cdp_port = raw_port
                except Exception:
                    cdp_port = 0
                accounts.append({
                    "id": str(item["id"]),
                    "name": str(item["name"]),
                    "browser": browser,
                    "proxy": proxy,
                    "note": note,
                    "color": color,
                    "custom_url": custom_url,
                    "group": group,
                    "window_size": window_size,
                    "user_agent": user_agent,
                    "login_email": login_email,
                    "login_password": login_password,
                    "vip_expire": vip_expire,
                    "pinned": pinned,
                    "cdp_port": cdp_port,
                    "muse_plan": muse_plan,
                    "quota_status": quota_status,
                    "quota_used_pct": quota_used_pct,
                    "extra_used_pct": extra_used_pct,
                    "quota_reset_day": quota_reset_day,
                    "extra_tokens_text": extra_tokens_text,
                    "agent_role": agent_role,
                    "agent_prompts": agent_prompts,
                })
    # 为未分配 CDP 端口的账号自动分配独立调试端口（9221 起）
    used = {a["cdp_port"] for a in accounts if a.get("cdp_port")}
    next_p = 9221
    for a in accounts:
        if not a.get("cdp_port"):
            while next_p in used:
                next_p += 1
            a["cdp_port"] = next_p
            used.add(next_p)
    return accounts


def save_accounts(accounts: list[dict]) -> None:
    _write_json(data_dir() / "accounts.json", accounts)


def new_account_id() -> str:
    return uuid.uuid4().hex[:12]


def next_account_name(accounts: list[dict]) -> str:
    n = len(accounts) + 1
    names = {a["name"] for a in accounts}
    while f"Muse 账号 {n}" in names:
        n += 1
    return f"Muse 账号 {n}"


def next_cdp_port(accounts: list[dict], start: int = 9221) -> int:
    used = {int(a.get("cdp_port") or 0) for a in accounts}
    p = start
    while p in used:
        p += 1
    return p


def clone_seed_profile_clean(src_acc_id: str, dst_acc_id: str, browser: str = "chrome") -> int:
    """从黄金母版复制扩展、书签与偏好设置到新账号，严格剥离 Cookie/登录态防串号。"""
    src_dir = profile_dir_for_browser(src_acc_id, browser)
    dst_dir = profile_dir_for_browser(dst_acc_id, browser)
    if not src_dir.is_dir():
        return 0
    dst_def = dst_dir / "Default"
    dst_def.mkdir(parents=True, exist_ok=True)
    src_def = src_dir / "Default"
    copied = 0
    # 仅复制非敏感环境资产：扩展目录、书签、外观偏好
    for rel_dir in ("Extensions", "Extension Rules", "Extension Scripts"):
        s = src_def / rel_dir
        t = dst_def / rel_dir
        if s.is_dir():
            shutil.copytree(s, t, dirs_exist_ok=True)
            copied += 1
    for rel_file in ("Bookmarks", "Preferences"):
        s = src_def / rel_file
        if s.is_file():
            shutil.copy2(s, dst_def / rel_file)
            copied += 1
    return copied


def delete_profile_dir(acc_id: str) -> None:
    shutil.rmtree(profiles_dir() / acc_id, ignore_errors=True)
    shutil.rmtree(edge_profiles_dir() / acc_id, ignore_errors=True)


def clean_profile_cache(acc_id: str, browser: str) -> int:
    pdir = profile_dir_for_browser(acc_id, browser)
    cleaned = 0
    cache_folder_names = {
        "Cache", "GPUCache", "Code Cache", "DawnCache",
        "ShaderCache", "GrShaderCache", "Service Worker/CacheStorage",
    }
    for base in (pdir, pdir / "Default"):
        if not base.is_dir():
            continue
        for name in cache_folder_names:
            target = base / name
            if target.is_dir():
                try:
                    shutil.rmtree(target, ignore_errors=True)
                    cleaned += 1
                except Exception:
                    pass
    return cleaned


def proxy_server_url(cfg: dict) -> str | None:
    if cfg.get("proxy_mode", "local") != "local":
        return None
    if cfg.get("proxy_enabled") is False:
        return None
    scheme = "socks5" if cfg.get("proxy_type") == "socks5" else "http"
    return f"{scheme}://{cfg['proxy_host']}:{cfg['proxy_port']}"


def system_proxy_url() -> str | None:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            try:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            except OSError:
                return None
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except (ImportError, OSError):
        return None
    server = str(server or "").strip()
    if not server:
        return None
    if "=" in server:
        parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
        server = parts.get("https") or parts.get("http") or ""
    if not server:
        return None
    if "://" not in server:
        server = "http://" + server
    return server


def fetch_proxy_url(cfg: dict) -> str | None:
    mode = cfg.get("proxy_mode", "local")
    if mode == "local":
        return proxy_server_url(cfg)
    if mode == "system":
        return system_proxy_url()
    return None


def effective_cfg(cfg: dict, acc: dict) -> dict:
    """将账号专属代理与指纹配置合并进全局配置。"""
    merged = dict(cfg)
    acc = acc or {}
    proxy = acc.get("proxy") or ""
    if proxy == "direct":
        merged["proxy_mode"] = "off"
    elif isinstance(proxy, dict):
        merged.update({
            "proxy_mode": "local",
            "proxy_enabled": True,
            "proxy_type": proxy.get("type", "http"),
            "proxy_host": proxy.get("host", "127.0.0.1"),
            "proxy_port": proxy.get("port", 7892),
            "proxy_username": proxy.get("username", ""),
            "proxy_password": proxy.get("password", ""),
        })
    if acc.get("window_size"):
        merged["window_size"] = acc["window_size"]
    if acc.get("user_agent"):
        merged["user_agent"] = acc["user_agent"]
    if acc.get("cdp_port"):
        merged["cdp_port"] = int(acc["cdp_port"])
    return merged


def build_chrome_args(
    cfg: dict,
    profile_dir: Path,
    extra_urls: list[str] | None = None,
    start_url: str | None = None,
) -> list[str]:
    """组装 Chrome / Edge 启动命令行参数（支持带账密代理扩展、未打包扩展全局挂载与独立 CDP 端口）。"""
    args = [
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-default-browser-prompt",
        "--password-store=basic",
    ]
    load_exts: list[str] = []
    for ext_path in cfg.get("unpacked_extensions") or []:
        ep = str(ext_path).strip()
        if ep and Path(ep).is_dir():
            load_exts.append(ep)

    url = proxy_server_url(cfg)
    proxy_user = str(cfg.get("proxy_username") or "").strip()
    proxy_pass = str(cfg.get("proxy_password") or "")
    if url and proxy_user:
        ext_dir = ensure_proxy_auth_extension(
            profile_dir=profile_dir,
            host=str(cfg.get("proxy_host", "127.0.0.1")),
            port=int(cfg.get("proxy_port", 7892)),
            username=proxy_user,
            password=proxy_pass,
            scheme=str(cfg.get("proxy_type", "http")),
        )
        load_exts.append(str(ext_dir))
    elif url:
        args.append(f"--proxy-server={url}")

    if load_exts:
        args.append(f"--load-extension={','.join(load_exts)}")

    cdp_port = int(cfg.get("cdp_port") or 0)
    if cfg.get("cdp_enabled", True) and cdp_port >= 1024:
        args.append(f"--remote-debugging-port={cdp_port}")

    if cfg.get("webrtc_protect"):
        args.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
    if cfg.get("lang_en"):
        args.append("--lang=en-US")
    if cfg.get("anti_fingerprint"):
        args.append("--disable-blink-features=AutomationControlled")
        args.append("--disable-features=OptimizationHints,TranslateUI")
    if cfg.get("window_size"):
        args.append(f"--window-size={cfg['window_size']}")
    if cfg.get("user_agent"):
        args.append(f"--user-agent={cfg['user_agent']}")

    dest_url = start_url or str(cfg.get("home_url", DEFAULT_HOME_URL))
    args.append(dest_url)
    for u in extra_urls or []:
        if u != dest_url:
            args.append(str(u))
    return args


def chrome_candidates() -> list[Path]:
    out: list[Path] = []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    for root in (pf, pf86, local):
        if root:
            out.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                    hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
                ) as key:
                    val, _ = winreg.QueryValueEx(key, None)
                    if val:
                        out.insert(0, Path(val))
            except OSError:
                pass
    except ImportError:
        pass
    return out


def find_chrome(cfg: dict) -> str:
    manual = str(cfg.get("chrome_path", "")).strip()
    if manual and Path(manual).is_file():
        return manual
    for cand in chrome_candidates():
        if cand.is_file():
            return str(cand)
    return ""


def edge_candidates() -> list[Path]:
    out: list[Path] = []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    for root in (pf, pf86, local):
        if root:
            out.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                    hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
                ) as key:
                    val, _ = winreg.QueryValueEx(key, None)
                    if val:
                        out.insert(0, Path(val))
            except OSError:
                pass
    except ImportError:
        pass
    return out


def find_edge(cfg: dict) -> str:
    manual = str(cfg.get("edge_path", "")).strip()
    if manual and Path(manual).is_file():
        return manual
    for cand in edge_candidates():
        if cand.is_file():
            return str(cand)
    return ""


def find_browser_exe(cfg: dict, browser: str) -> str:
    if browser == "edge":
        return find_edge(cfg)
    return find_chrome(cfg)


def export_backup(dest: str, include_profiles: bool) -> None:
    import zipfile
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("config.json", "accounts.json"):
            f = data_dir() / name
            if f.is_file():
                z.write(f, f"DeckData/{name}")
        if include_profiles:
            for base in ("chrome-profiles", "edge-profiles"):
                root = data_dir() / base
                if root.is_dir():
                    for f in sorted(root.rglob("*")):
                        if f.is_file():
                            z.write(f, f"DeckData/{base}/{f.relative_to(root)}")


def import_backup(src: str) -> dict:
    import time
    import zipfile
    with zipfile.ZipFile(src) as z:
        names = [n for n in z.namelist() if n.startswith("DeckData/") and not n.endswith("/")]
        if "DeckData/accounts.json" not in names:
            raise ValueError("选中的文件中没有找到 MuseDeck 的账号数据，不是有效备份文件")
        safety = data_dir() / f"backup-before-import-{time.strftime('%Y%m%d-%H%M%S')}"
        safety.mkdir(parents=True, exist_ok=True)
        for name in ("config.json", "accounts.json"):
            cur = data_dir() / name
            if cur.is_file():
                shutil.copy2(cur, safety / name)
        has_profiles = False
        base = data_dir().resolve()
        for name in names:
            rel = name[len("DeckData/"):]
            target = (data_dir() / rel).resolve()
            if base not in target.parents and target != base:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as fsrc, open(target, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            if "/profiles/" in name or "-profiles/" in name:
                has_profiles = True
    return {"accounts": len(load_accounts()), "profiles": has_profiles}
