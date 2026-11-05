"""Web server (play money, single local player): python -m royalfruits.web [--port 8000]

    /               lobby with the games and the shared balance
    /fruits, /cats, /miner  the three games
    /api/lobby, /api/<game>/state|spin|play|collect|gamble, /api/reset
"""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .casino import GAMES, Casino, UnknownGame
from .gamble import GambleError
from .wallet import InsufficientFunds

ROOT = Path(__file__).resolve().parent.parent
SAVE_PATH = ROOT / "session.json"
STATIC_DIRS = {"/assets/": ROOT / "assets", "/": ROOT / "web"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json"}

CASINO = Casino(save_path=SAVE_PATH)
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        elif path.strip("/") in GAMES:
            path = "/" + GAMES[path.strip("/")]["page"]
        for prefix, base in STATIC_DIRS.items():
            if path.startswith(prefix):
                f = (base / unquote(path[len(prefix):])).resolve()
                if base.resolve() in f.parents and f.is_file() and f.suffix in TYPES:
                    return self._send(200, f.read_bytes(), TYPES[f.suffix])
        self._send(404, {"error": "not found"})

    def do_GET(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        try:
            if parts == ["api", "lobby"]:
                with LOCK:
                    out = CASINO.lobby()
                    CASINO.save()
                return self._send(200, out)
            if len(parts) == 3 and parts[0] == "api" and parts[2] == "state":
                with LOCK:
                    out = CASINO.game(parts[1]).state()
                    CASINO.save()
                return self._send(200, out)
        except UnknownGame as e:
            return self._send(404, {"error": str(e)})
        self._static(urlparse(self.path).path)

    def do_POST(self):
        global CASINO
        parts = urlparse(self.path).path.strip("/").split("/")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n <= 4096 else {}
            with LOCK:
                if parts == ["api", "reset"]:
                    path = CASINO.save_path
                    if path:
                        path.unlink(missing_ok=True)
                    CASINO = Casino(save_path=path)
                    return self._send(200, CASINO.lobby())
                if len(parts) != 3 or parts[0] != "api":
                    return self._send(404, {"error": "not found"})
                game, action = CASINO.game(parts[1]), parts[2]
                if action == "spin" and hasattr(game, "spin"):
                    out = game.spin(body.get("bet"))
                elif action == "play" and hasattr(game, "play"):
                    out = game.play(body.get("bet"))
                elif action == "collect":
                    out = game.collect()
                elif action == "gamble" and hasattr(game, "pick"):
                    out = game.pick(body.get("color"))
                else:
                    return self._send(404, {"error": "not found"})
                CASINO.save()
            self._send(200, out)
        except UnknownGame as e:
            self._send(404, {"error": str(e)})
        except (InsufficientFunds, GambleError, ValueError, TypeError, ArithmeticError) as e:
            self._send(400, {"error": str(e) or "invalid request"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Casino running at http://{a.host}:{a.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
