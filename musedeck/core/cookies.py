"""单账号轻量会话/Cookie 导出与导入（不含缓存，几百 KB 秒传免登录）。"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from . import store

SESSION_REL_FILES = [
    "Local State",
    "Default/Network/Cookies",
    "Default/Cookies",
    "Default/Preferences",
    "Default/Web Data",
]


def export_account_session(acc_id: str, browser: str, dest_zip: str) -> dict:
    """仅导出指定账号的核心登录会话文件（Cookies + Local State + Preferences）。"""
    pdir = store.profile_dir_for_browser(acc_id, browser)
    if not pdir.is_dir():
        raise FileNotFoundError("该账号尚未初始化浏览器数据目录")
    written = 0
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in SESSION_REL_FILES:
            src = pdir / rel
            if src.is_file():
                z.write(src, f"session/{rel}")
                written += 1
        ls_dir = pdir / "Default" / "Local Storage" / "leveldb"
        if ls_dir.is_dir():
            for f in sorted(ls_dir.rglob("*")):
                if f.is_file() and f.name != "LOCK":
                    z.write(f, f"session/Default/Local Storage/leveldb/{f.relative_to(ls_dir)}")
                    written += 1
    if written == 0:
        raise ValueError("未找到可导出的登录态文件，请先打开该账号并登录一次")
    return {"files": written, "path": dest_zip}


def import_account_session(acc_id: str, browser: str, src_zip: str) -> dict:
    """将轻量会话包导入指定账号（请确保目标账号浏览器已关闭）。"""
    pdir = store.profile_dir_for_browser(acc_id, browser)
    pdir.mkdir(parents=True, exist_ok=True)
    base = pdir.resolve()
    restored = 0
    with zipfile.ZipFile(src_zip, "r") as z:
        names = [n for n in z.namelist() if n.startswith("session/") and not n.endswith("/")]
        if not names:
            raise ValueError("不是有效的 MuseDeck 轻量会话包")
        for name in names:
            rel = name[len("session/"):]
            target = (pdir / rel).resolve()
            if base not in target.parents and target != base:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as fsrc, open(target, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            restored += 1
    return {"files": restored}
