"""test_v3_core.py: 验证 MuseDeck 核心引擎（账密代理扩展、窗口平铺、扩展解析、CDP 额度抓取、HTTP 调度 API）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from musedeck.api_server import LocalApiServer
from musedeck.core import cdp, cookies, extensions, proxy_auth, store, window_mgr


class TestV3Core(unittest.TestCase):
    def test_theme_normalization(self):
        self.assertEqual(store.normalize_theme_key("dark"), "dark")
        self.assertEqual(store.normalize_theme_key("light"), "light")
        self.assertEqual(store.normalize_theme_key("LIGHT"), "light")
        self.assertEqual(store.normalize_theme_key(""), "dark")

    def test_proxy_auth_extension_and_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "prof1"
            pdir.mkdir()
            cfg = {
                "proxy_mode": "local",
                "proxy_enabled": True,
                "proxy_type": "http",
                "proxy_host": "45.12.34.56",
                "proxy_port": 8080,
                "proxy_username": "res_user",
                "proxy_password": "res_pass_123",
                "home_url": "https://muse.ai",
                "webrtc_protect": True,
                "lang_en": True,
                "anti_fingerprint": True,
                "window_size": "1440,900",
                "user_agent": "CustomAgent/3.0",
            }
            args = store.build_chrome_args(cfg, pdir)
            ext_arg = next((a for a in args if a.startswith("--load-extension=")), None)
            self.assertIsNotNone(ext_arg)
            ext_dir = Path(ext_arg.split("=", 1)[1])
            self.assertTrue((ext_dir / "manifest.json").is_file())
            self.assertTrue((ext_dir / "background.js").is_file())
            bg = (ext_dir / "background.js").read_text(encoding="utf-8")
            self.assertIn("res_user", bg)
            self.assertIn("res_pass_123", bg)
            self.assertIn("45.12.34.56", bg)
            self.assertIn("--window-size=1440,900", args)
            self.assertIn("--user-agent=CustomAgent/3.0", args)
            # 启用账密扩展时不应重复传 --proxy-server
            self.assertFalse(any(a.startswith("--proxy-server=") for a in args))

    def test_compute_tile_rects_no_overlap(self):
        work = (0, 0, 1920, 1040)
        for n in (1, 2, 4, 6):
            rects = window_mgr.compute_tile_rects(n, work, layout="grid", gap=8)
            self.assertEqual(len(rects), n)
            for x, y, w, h in rects:
                self.assertGreaterEqual(x, work[0])
                self.assertGreaterEqual(y, work[1])
                self.assertLessEqual(x + w, work[2])
                self.assertLessEqual(y + h, work[3])
            # 两两互不重叠
            for i in range(n):
                x1, y1, w1, h1 = rects[i]
                for j in range(i + 1, n):
                    x2, y2, w2, h2 = rects[j]
                    overlap = not (x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1)
                    self.assertFalse(overlap, f"Rects {i} and {j} overlap for n={n}")

    def test_extension_manifest_localized_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            ud = Path(tmp)
            ext_id = "abcdefghijklmnop1234567890abcdef"
            vdir = ud / "Default" / "Extensions" / ext_id / "1.2.3_0"
            (vdir / "_locales" / "zh_CN").mkdir(parents=True)
            (vdir / "manifest.json").write_text(
                json.dumps({"name": "__MSG_extName__", "version": "1.2.3", "description": "Test desc"}),
                encoding="utf-8",
            )
            (vdir / "_locales" / "zh_CN" / "messages.json").write_text(
                json.dumps({"extName": {"message": "工作流辅助插件"}}),
                encoding="utf-8",
            )
            found = extensions.list_source_extensions(user_data_dir=ud)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["name"], "工作流辅助插件")
            self.assertEqual(found[0]["version"], "1.2.3")

    def test_cookie_session_export_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig_data_dir = store.data_dir
            try:
                store.data_dir = lambda: Path(tmp)
                pdir = store.profile_dir_for("acc_a")
                (pdir / "Default" / "Network").mkdir(parents=True)
                (pdir / "Local State").write_text('{"os_crypt":{}}', encoding="utf-8")
                (pdir / "Default" / "Network" / "Cookies").write_bytes(b"SQLITE_MOCK")

                dest = str(Path(tmp) / "sess.zip")
                exp = cookies.export_account_session("acc_a", "chrome", dest)
                self.assertEqual(exp["files"], 2)

                imp = cookies.import_account_session("acc_b", "chrome", dest)
                self.assertEqual(imp["files"], 2)
                pdir_b = store.profile_dir_for("acc_b")
                self.assertEqual((pdir_b / "Default" / "Network" / "Cookies").read_bytes(), b"SQLITE_MOCK")
            finally:
                store.data_dir = orig_data_dir

    def test_seed_clone_strips_cookies_and_cdp_unpacked_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig_data_dir = store.data_dir
            try:
                store.data_dir = lambda: Path(tmp)
                src = store.profile_dir_for("seed1")
                (src / "Default" / "Extensions" / "ext1").mkdir(parents=True)
                (src / "Default" / "Extensions" / "ext1" / "manifest.json").write_text("{}", encoding="utf-8")
                (src / "Default" / "Bookmarks").write_text('{"roots":{}}', encoding="utf-8")
                (src / "Default" / "Network").mkdir(parents=True)
                (src / "Default" / "Network" / "Cookies").write_bytes(b"SECRET_COOKIE")

                copied = store.clone_seed_profile_clean("seed1", "child1", "chrome")
                self.assertGreaterEqual(copied, 2)
                dst = store.profile_dir_for("child1")
                self.assertTrue((dst / "Default" / "Extensions" / "ext1" / "manifest.json").is_file())
                self.assertTrue((dst / "Default" / "Bookmarks").is_file())
                # 严格剥离 Cookie，绝不串号
                self.assertFalse((dst / "Default" / "Network" / "Cookies").exists())

                unpacked = Path(tmp) / "museflow_ext"
                unpacked.mkdir()
                args = store.build_chrome_args(
                    {"proxy_mode": "off", "cdp_port": 9225, "unpacked_extensions": [str(unpacked)]},
                    dst,
                )
                self.assertIn("--remote-debugging-port=9225", args)
                self.assertIn(f"--load-extension={unpacked}", args)
            finally:
                store.data_dir = orig_data_dir

    def test_local_agent_http_api(self):
        import urllib.request

        class _MockDeck:
            accounts = [{"id": "n1", "name": "Node 1", "group": "VIP", "browser": "chrome", "cdp_port": 9221}]

            def get_runtime_state(self):
                return {"n1": {"running": False, "mem_mb": 0, "page_title": "", "need_login": False}}

        srv = LocalApiServer(_MockDeck(), port=18945)
        self.assertTrue(srv.start())
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:18945/api/health", timeout=3) as r:
                health = json.loads(r.read().decode("utf-8"))
                self.assertTrue(health["ok"])
                self.assertEqual(health["app"], "MuseDeck")
            with opener.open("http://127.0.0.1:18945/api/nodes", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
                self.assertEqual(len(data["nodes"]), 1)
                self.assertEqual(data["nodes"][0]["cdp_port"], 9221)
        finally:
            srv.stop()

    def test_parse_muse_quota_from_dom(self):
        items = [
            {"label": "Free plan", "value": 0, "text": "Free plan\nWeekly limit resets on Dec 2\n0% used"},
            {"label": "Additional tokens", "value": 0, "text": "Additional tokens\nNever expires\n0% used (1B tokens left)"},
        ]
        res = cdp.parse_muse_quota_items(items)
        self.assertIsNotNone(res)
        self.assertEqual(res["muse_plan"], "free")
        self.assertEqual(res["quota_used_pct"], 0)
        self.assertEqual(res["extra_used_pct"], 0)
        self.assertEqual(res["quota_status"], "ok")
        self.assertEqual(res["quota_reset_day"], "Dec 2")
        self.assertEqual(res["extra_tokens_text"], "1B tokens left")

        high = cdp.parse_muse_quota_items([{"label": "Power plan", "value": 100, "text": "Power plan\n100% used"}])
        self.assertEqual(high["muse_plan"], "power")
        self.assertEqual(high["quota_status"], "empty")


if __name__ == "__main__":
    unittest.main()
