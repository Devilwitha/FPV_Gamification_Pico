import argparse
import json
import os
import shutil
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SOURCE_DIR = os.path.join(PROJECT_ROOT, "source")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

ROUTE_TO_FILE = {
    "/": "index.html",
    "/admin": "admin_dashboard.html",
    "/admin-update": "admin_update.html",
    "/admin-simulate": "admin_simulate.html",
    "/admin-profiles": "admin_profiles.html",
    "/admin-system": "admin_system.html",
    "/admin-challenges": "admin_challenges.html",
    "/challenges-view": "challenges_view.html",
}


class FpvDevHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DATA_DIR, **kwargs)

    def end_headers(self):
        # Disable browser caching so HTML/CSS/JS changes are visible immediately.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ROUTE_TO_FILE:
            self.path = "/" + ROUTE_TO_FILE[path]
            return super().do_GET()

        if path == "/data":
            return self._send_json(
                {
                    "score": 128,
                    "highscore": 420,
                    "highscore_player": "Bollshii",
                    "history": ["flip", "roll", "spin"],
                    "trick_tuning_profile": "freestyle",
                    "firmware_version": "dev-local",
                    "pending_highscore": False,
                    "pending_highscore_score": 0,
                }
            )

        if path == "/version":
            return self._send_json({"version": "dev-local"})

        if path == "/system-info":
            return self._send_json(
                {
                    "mem_free": 123456,
                    "mem_alloc": 65432,
                    "uptime_s": 3600,
                    "ssid": "FPV_Gamification_Pico",
                    "ip": "127.0.0.1",
                    "ota_active": False,
                    "ota_received_chunks": 0,
                    "ota_total_chunks": 0,
                    "trick_tuning_profile": "freestyle",
                    "developer_mode": True,
                    "firmware_version": "dev-local",
                }
            )

        if path in {"/download", "/download-debug"}:
            payload = b"local test server download\n"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="local-dev.txt"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/profiles-list":
            return self._send_json(
                {
                    "profiles": [
                        {"name": "beginner", "active": False},
                        {"name": "freestyle", "active": True},
                        {"name": "aggressive", "active": False},
                    ]
                }
            )

        if path == "/download-profile":
            params = parse_qs(parsed.query)
            name = (params.get("name", ["profile"])[0] or "profile").strip()
            content = '{"name": "%s", "source": "local-dev-server"}\n' % name
            payload = content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{name}.pro"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path in {
            "/set-highscore-name",
            "/confirm-highscore",
            "/set-trick-profile",
            "/reset-highscore",
            "/simulate-trick",
            "/restart-pico",
            "/set-developer-mode",
            "/delete-profile",
            "/apply-profile",
            "/finalize-upload",
        }:
            return self._send_json({"ok": True, "mock": True})

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/upload-chunk", "/create-profile"}:
            # Read and ignore incoming body in local mock mode.
            content_len = int(self.headers.get("Content-Length", "0") or 0)
            if content_len > 0:
                self.rfile.read(content_len)
            return self._send_json({"ok": True, "mock": True})

        return self._send_json({"ok": False, "error": "Unsupported endpoint in local test server."}, status=404)

    def _send_json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def clone_source_to_data(refresh=False):
    if refresh and os.path.isdir(DATA_DIR):
        shutil.rmtree(DATA_DIR)

    if not os.path.isdir(DATA_DIR):
        shutil.copytree(SOURCE_DIR, DATA_DIR)
        print("[WEB] Created data clone from source.")


def main():
    parser = argparse.ArgumentParser(description="Local dev web server for FPV_Gamification_Pico HTML testing.")
    parser.add_argument("--host", default="127.0.0.1", help="Host/IP to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument(
        "--refresh-data",
        action="store_true",
        help="Delete data folder and clone a fresh copy from source before starting.",
    )
    args = parser.parse_args()

    if not os.path.isdir(SOURCE_DIR):
        raise SystemExit(f"Source folder not found: {SOURCE_DIR}")

    clone_source_to_data(refresh=args.refresh_data)

    server = ThreadingHTTPServer((args.host, args.port), FpvDevHandler)
    print(f"Serving test data from: {DATA_DIR}")
    print(f"Cloned from source: {SOURCE_DIR}")
    print(f"Open: http://{args.host}:{args.port}/")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
