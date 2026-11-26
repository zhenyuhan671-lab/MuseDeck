"""test_store.py: 测试配置读取、账号增删、命令行参数生成与路径计算。"""

import os
import sys
import unittest
from pathlib import Path

# 添加项目目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from musedeck.core import store


class TestStore(unittest.TestCase):
    def test_default_config(self):
        cfg = store.load_config()
        self.assertIn("proxy_mode", cfg)
        self.assertIn("home_url", cfg)
        self.assertTrue(cfg["home_url"].startswith("http"))

    def test_build_chrome_args(self):
        cfg = {
            "proxy_mode": "local",
            "proxy_type": "http",
            "proxy_host": "127.0.0.1",
            "proxy_port": 7892,
            "home_url": "https://muse.ai",
            "webrtc_protect": True,
            "lang_en": True,
            "anti_fingerprint": True,
        }
        profile = Path("D:/test_profile")
        args = store.build_chrome_args(cfg, profile)

        self.assertIn(f"--user-data-dir={profile}", args)
        self.assertIn("--proxy-server=http://127.0.0.1:7892", args)
        self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", args)
        self.assertIn("--lang=en-US", args)
        self.assertIn("--disable-blink-features=AutomationControlled", args)
        self.assertIn("https://muse.ai", args)

    def test_build_chrome_args_custom_url(self):
        cfg = {
            "proxy_mode": "off",
            "home_url": "https://muse.ai",
            "webrtc_protect": False,
            "lang_en": False,
            "anti_fingerprint": False,
        }
        profile = Path("D:/test_profile")
        args = store.build_chrome_args(cfg, profile, start_url="https://muse.ai/workspace")
        self.assertIn("https://muse.ai/workspace", args)
        # 不应有 proxy 参数
        self.assertFalse(any(a.startswith("--proxy-server=") for a in args))

    def test_effective_cfg_account_override(self):
        global_cfg = {
            "proxy_mode": "local",
            "proxy_type": "http",
            "proxy_host": "127.0.0.1",
            "proxy_port": 7892,
        }
        # 账号设为直连
        acc_direct = {"proxy": "direct"}
        res = store.effective_cfg(global_cfg, acc_direct)
        self.assertEqual(res["proxy_mode"], "off")

        # 账号设为专属独立代理
        acc_custom = {"proxy": {"type": "socks5", "host": "127.0.0.1", "port": 1080}}
        res2 = store.effective_cfg(global_cfg, acc_custom)
        self.assertEqual(res2["proxy_mode"], "local")
        self.assertEqual(res2["proxy_type"], "socks5")
        self.assertEqual(res2["proxy_port"], 1080)


if __name__ == "__main__":
    unittest.main()
