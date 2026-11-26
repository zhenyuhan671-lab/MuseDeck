"""Chrome 本地扩展解析：从 manifest.json 与 _locales 提取真实名称与版本。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def main_chrome_user_data() -> Path | None:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    p = Path(local) / "Google" / "Chrome" / "User Data"
    return p if p.is_dir() else None


def resolve_localized_string(ext_version_dir: Path, raw_value: str) -> str:
    """解析 Chrome 扩展中的 __MSG_xxx__ 占位符。"""
    if not raw_value:
        return ""
    m = re.fullmatch(r"__MSG_([A-Za-z0-9_@]+)__", raw_value.strip())
    if not m:
        return raw_value.strip()
    key = m.group(1)
    locales_dir = ext_version_dir / "_locales"
    if not locales_dir.is_dir():
        return raw_value.strip()

    preferred = ["zh_CN", "zh", "en", "en_US", "en_GB"]
    candidates = [locales_dir / loc / "messages.json" for loc in preferred]
    for sub in sorted(locales_dir.iterdir()):
        cand = sub / "messages.json"
        if cand not in candidates:
            candidates.append(cand)

    for msg_file in candidates:
        if not msg_file.is_file():
            continue
        try:
            data = json.loads(msg_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 大小写不敏感匹配 key
        for k, v in data.items():
            if k.lower() == key.lower() and isinstance(v, dict) and v.get("message"):
                return str(v["message"]).strip()
    return raw_value.strip()


def parse_extension_metadata(ext_dir: Path, ext_id: str) -> dict:
    """读取扩展最新版本的 manifest.json，提取真实名称、版本与描述。"""
    version_dirs = [d for d in ext_dir.iterdir() if d.is_dir()] if ext_dir.is_dir() else []
    version_dirs.sort(key=lambda d: d.name, reverse=True)
    for vdir in version_dirs:
        mf = vdir / "manifest.json"
        if not mf.is_file():
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw_name = str(data.get("name") or "")
        raw_desc = str(data.get("description") or "")
        name = resolve_localized_string(vdir, raw_name) or ext_id
        desc = resolve_localized_string(vdir, raw_desc)
        version = str(data.get("version") or vdir.name)
        return {
            "id": ext_id,
            "name": name,
            "version": version,
            "description": desc[:120],
        }
    return {"id": ext_id, "name": ext_id, "version": "", "description": ""}


def list_source_extensions(user_data_dir: Path | None = None) -> list[dict]:
    """扫描主 Chrome 安装的扩展并解析出真实名称与版本号。"""
    ud = user_data_dir if user_data_dir is not None else main_chrome_user_data()
    if ud is None or not ud.is_dir():
        return []
    profiles = [ud / "Default"] + sorted(ud.glob("Profile *"))
    found: dict[str, dict] = {}
    for prof in profiles:
        ext_root = prof / "Extensions"
        if not ext_root.is_dir():
            continue
        for ext_dir in sorted(ext_root.iterdir()):
            if not ext_dir.is_dir():
                continue
            ext_id = ext_dir.name
            if ext_id in found or len(ext_id) < 16 or ext_id.startswith("Temp"):
                continue
            meta = parse_extension_metadata(ext_dir, ext_id)
            found[ext_id] = meta
    return sorted(found.values(), key=lambda e: e["name"].lower())


def store_url(ext_id: str) -> str:
    return f"https://chromewebstore.google.com/detail/{ext_id}"
