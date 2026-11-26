"""MuseDeck 本地 HTTP Agent 调度 API 服务（默认监听 127.0.0.1:18900）。

供外部 Python 脚本、Playwright、n8n 或 AI Agent 直接调度已登录的真实隔离浏览器节点：
- GET  /api/health
- GET  /api/nodes
- POST /api/nodes/<id>/start
- POST /api/nodes/<id>/stop
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import APP_NAME, __version__


class LocalApiServer:
    def __init__(self, deck_api, host: str = "127.0.0.1", port: int = 18900):
        self._deck_api = deck_api
        self.host = host
        self.port = int(port)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._server is not None:
            return True
        deck_api = self._deck_api

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                return  # 静默标准日志

            def _send_json(self, code: int, payload: dict):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/health":
                    return self._send_json(200, {"ok": True, "app": APP_NAME, "version": __version__})
                if self.path == "/api/nodes":
                    rt = deck_api.get_runtime_state()
                    nodes = []
                    for acc in deck_api.accounts:
                        st = rt.get(acc["id"], {})
                        nodes.append({
                            "id": acc["id"],
                            "name": acc["name"],
                            "group": acc.get("group", "默认分组"),
                            "browser": acc.get("browser", "chrome"),
                            "cdp_port": acc.get("cdp_port", 0),
                            "running": bool(st.get("running", False)),
                            "mem_mb": st.get("mem_mb", 0),
                            "page_title": st.get("page_title", ""),
                            "need_login": bool(st.get("need_login", False)),
                        })
                    return self._send_json(200, {"ok": True, "nodes": nodes})
                return self._send_json(404, {"ok": False, "error": "not_found"})

            def do_POST(self):
                parts = [p for p in self.path.strip("/").split("/") if p]
                if len(parts) == 4 and parts[0] == "api" and parts[1] == "nodes":
                    node_id, action = parts[2], parts[3]
                    if action == "start":
                        res = deck_api.open_account(node_id, force=True)
                        acc = next((a for a in deck_api.accounts if a["id"] == node_id), None)
                        port = int(acc.get("cdp_port", 0)) if acc else 0
                        return self._send_json(
                            200 if res.get("ok") else 400,
                            {**res, "cdp_port": port, "cdp_endpoint": f"http://127.0.0.1:{port}" if port else ""},
                        )
                    if action == "stop":
                        res = deck_api.close_account(node_id)
                        return self._send_json(200, res)
                return self._send_json(404, {"ok": False, "error": "not_found"})

        try:
            self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            return True
        except OSError:
            self._server = None
            return False

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
            self._thread = None
