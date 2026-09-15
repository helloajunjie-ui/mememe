"""MCP 独立服务 HTTP API（127.0.0.1:8767）。

素月侧只依赖本服务：
- GET    /health           探活
- GET    /mcp/servers      软件接口列表 + 激活状态
- GET    /mcp/active       当前已激活 server 及工具名
- GET    /mcp/tools        工具明细（?server=xxx）
- GET    /mcp/schemas      OpenAI schema（?server=xxx，供素月装配 tools）
- POST   /mcp/activate     激活（拉取工具清单，?force 重拉）
- POST   /mcp/deactivate   释放
- POST   /mcp/call         工具调用代理 {server, tool, args}
"""
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_core import McpGateway  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
gateway = McpGateway(
    config_path=os.path.join(BASE, "config", "mcp.json"),
    data_path=os.path.join(BASE, "data", "active.json"),
)
PORT = int(os.environ.get("MCP_SERVICE_PORT", "8767"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默默认访问日志，请求日志自己写
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send({"ok": True})

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _dispatch(self, method):
        t0 = time.time()
        try:
            u = urlparse(self.path)
            p = u.path
            q = parse_qs(u.query)
            if method == "GET":
                if p == "/health":
                    self._send({"ok": True, "service": "mcp-service",
                                "servers": len(gateway.mgr.servers)})
                elif p == "/mcp/servers":
                    self._send({"ok": True, "servers": gateway.servers_info()})
                elif p == "/mcp/active":
                    self._send(gateway.tools())
                elif p == "/mcp/tools":
                    server = (q.get("server") or [""])[0]
                    self._send(gateway.tools(server or None))
                elif p == "/mcp/schemas":
                    server = (q.get("server") or [""])[0]
                    self._send(gateway.schemas(server))
                elif p == "/mcp/secrets":
                    # 只返回键名（值永不回传）：?server=x 指定接口，留空返回全部
                    server = (q.get("server") or [""])[0]
                    if server:
                        self._send({"ok": True, "server": server,
                                    "keys": gateway.secret_keys(server)})
                    else:
                        self._send({"ok": True,
                                    "keys": {s: gateway.secret_keys(s)
                                             for s in gateway.mgr.servers}})
                else:
                    self._send({"ok": False, "error": f"未知路径: {p}"}, 404)
            else:
                body = self._read_json()
                if p == "/mcp/activate":
                    self._send(gateway.activate(body.get("server", ""),
                                                force=bool(body.get("force"))))
                elif p == "/mcp/deactivate":
                    self._send(gateway.deactivate(body.get("server", "")))
                elif p == "/mcp/call":
                    self._send(gateway.call(body.get("server", ""),
                                            body.get("tool", ""),
                                            body.get("args") or {}))
                elif p == "/mcp/add":
                    self._send(gateway.add(body.get("name", ""),
                                           desc=body.get("desc", ""),
                                           command=body.get("command"),
                                           args=body.get("args"),
                                           env=body.get("env"),
                                           url=body.get("url"),
                                           transport=body.get("transport", "http"),
                                           install_mode=body.get("install_mode", ""),
                                           pip=body.get("pip", "")))
                elif p == "/mcp/remove":
                    self._send(gateway.remove(body.get("name", "")))
                elif p == "/mcp/secrets":
                    # 存/改凭据 {server, key, value}
                    self._send(gateway.set_secret(body.get("server", ""),
                                                  body.get("key", ""),
                                                  str(body.get("value", ""))))
                elif p == "/mcp/secrets/remove":
                    self._send(gateway.remove_secret(body.get("server", ""),
                                                     body.get("key", "")))
                elif p == "/mcp/deps/assess":
                    # 依赖/凭据评估（素月据此通知用户是否安装）
                    self._send(gateway.analyze_deps(body.get("server", "")))
                elif p == "/mcp/deps/install":
                    # 执行依赖安装（须先经用户确认）
                    self._send(gateway.install_deps(body.get("server", ""), upgrade=bool(body.get("upgrade"))))
                else:
                    self._send({"ok": False, "error": f"未知路径: {p}"}, 404)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            print(f"[mcp-service] {method} {self.path} ({time.time()-t0:.2f}s)", flush=True)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def _idle_sweeper():
    """后台清扫：每 60s 检查空闲超时的激活软件接口，自动释放（30 分钟无调用默认）。"""
    log_path = os.path.join(BASE, "data", "service.log")
    while True:
        time.sleep(60)
        try:
            released = gateway.idle_sweep()
            if released:
                line = (f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 空闲自动释放: "
                        f"{', '.join(released)}（{gateway.idle_timeout_min} 分钟无调用，"
                        f"需要时素月可重新 mcp_connect）")
                print(line, flush=True)
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            pass


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=_idle_sweeper, daemon=True).start()
    print(f"[mcp-service] listening on http://127.0.0.1:{PORT}", flush=True)
    print(f"[mcp-service] servers: {', '.join(gateway.mgr.servers) or '(空)'}", flush=True)
    print(f"[mcp-service] 空闲自动释放: {gateway.idle_timeout_min} 分钟无调用自动释放", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
