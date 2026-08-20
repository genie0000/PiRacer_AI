"""Standalone browser API for file-backed PiRacer autonomous control."""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import donkeycar as dk

from dashboard_control import CommandRejected, ControlStateStore


class DashboardHTTPServer(ThreadingHTTPServer):
    control_store: ControlStateStore
    token: str
    interface_path: Path


class Handler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer
    server_version = "PiRacerDashboard/2.0"

    def do_GET(self) -> None:
        if self.path in {"/", "/WebInterface.html"}:
            try:
                body = self.server.interface_path.read_bytes()
            except OSError:
                self._send_json(500, {"detail": "WebInterface.html not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/status":
            self._send_json(200, self.server.control_store.snapshot())
            return
        self._send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/control":
            self._send_json(404, {"detail": "not found"})
            return
        expected = f"Bearer {self.server.token}"
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self._send_json(401, {"accepted": False, "reason": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise CommandRejected("request body must be an object")
            self._send_json(200, self.server.control_store.apply(payload))
        except (UnicodeDecodeError, json.JSONDecodeError, CommandRejected, ValueError) as exc:
            self._send_json(400, {"accepted": False, "reason": str(exc)})

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        print("dashboard-server: " + (format % args))


def main() -> None:
    cfg = dk.load_config()
    if not cfg.DASHBOARD_CONTROL_ENABLED:
        raise ValueError("Set DASHBOARD_CONTROL_ENABLED = True in myconfig.py")
    if not cfg.DASHBOARD_CONTROL_TOKEN:
        raise ValueError("Set DASHBOARD_CONTROL_TOKEN in myconfig.py")
    server = DashboardHTTPServer((cfg.DASHBOARD_CONTROL_HOST, cfg.DASHBOARD_CONTROL_PORT), Handler)
    server.control_store = ControlStateStore(
        Path(cfg.DASHBOARD_CONTROL_STATE_PATH),
        cfg.DASHBOARD_HEARTBEAT_TIMEOUT_S,
        cfg.DASHBOARD_MAX_SPEED_MPS,
    )
    server.token = cfg.DASHBOARD_CONTROL_TOKEN
    server.interface_path = Path(__file__).with_name("WebInterface.html")
    print(f"Dashboard server: http://{cfg.DASHBOARD_CONTROL_HOST}:{cfg.DASHBOARD_CONTROL_PORT}/")
    print(f"Control state: {cfg.DASHBOARD_CONTROL_STATE_PATH}")
    server.serve_forever(poll_interval=0.2)


if __name__ == "__main__":
    main()
