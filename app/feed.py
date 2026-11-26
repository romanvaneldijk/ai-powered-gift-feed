"""The daily swipe game: a tiny local web server that opens in your browser.

    python3 app/feed.py          # today's deck from me/
    python3 app/feed.py --demo   # try it with example cards, nothing is saved to me/

The server stops by itself when you finish the deck, or after 30 idle minutes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import giftfeed as gf  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
IDLE_SECONDS = 30 * 60


class FeedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, paths: gf.Paths):
        super().__init__(addr, Handler)
        self.paths = paths
        self.settings = gf.load_settings(paths)
        self.deck = gf.build_deck(self.settings, gf.load_cards(paths))
        self.last_seen = time.monotonic()
        self.finished = False
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server: FeedServer

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def do_GET(self):
        self.server.last_seen = time.monotonic()
        if self.path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/deck":
            self._json(self._summary() | {"deck": self.server.deck})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        self.server.last_seen = time.monotonic()
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        if self.path == "/api/swipe":
            if body.get("id") not in {c["id"] for c in self.server.deck}:
                return self._json({"error": "card is not in today's deck"}, 400)
            try:
                with self.server.lock:
                    gf.record_swipe(self.server.paths, body["id"], body.get("action", ""), body.get("answer"))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True} | self._summary())
        if self.path == "/api/done":
            with self.server.lock:
                state = gf.update_streak(self.server.paths)
            self.server.finished = True
            return self._json({"ok": True, "streak": state.get("streak", 1)} | self._summary())
        self._json({"error": "not found"}, 404)

    def _summary(self) -> dict:
        p = self.server.paths
        cards = gf.load_cards(p)
        have = gf.wishlist_counts(cards)
        state = gf.read_json(p.state, {})
        taste = gf.taste_stats(gf.load_swipes(p))["taste_card"]
        return {
            "wishlist": {t: {"have": have[t]} for t in gf.TIERS},
            "streak": state.get("streak", 0),
            "taste": taste,
            "items": {
                t: [{k: c.get(k) for k in ("id", "title", "shop", "price", "url", "status")} for c in items]
                for t, items in gf.wishlist_items(cards).items()
            },
            "tier_ranges": self.server.settings["tiers"],
        }


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def demo_paths() -> gf.Paths:
    tmp = Path(tempfile.mkdtemp(prefix="giftfeed-demo-"))
    shutil.copytree(gf.ROOT / "examples", tmp, dirs_exist_ok=True)
    return gf.paths(tmp)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="use example cards in a temporary folder")
    ap.add_argument("--me", help="data folder (default: me/)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)

    paths = demo_paths() if args.demo else gf.paths(args.me)
    server = FeedServer(("127.0.0.1", free_port(args.port)), paths)
    gifts = [c for c in server.deck if c["type"] == "gift"]
    if not gifts:
        print("No new cards to swipe. Run /scout first to find gift ideas.")
        return
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Today's deck: {len(gifts)} gift{'' if len(gifts) == 1 else 's'}. Swipe at {url}")
    print("The page closes the game by itself when you're done (or press Ctrl+C).")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while not server.finished and time.monotonic() - server.last_seen < IDLE_SECONDS:
            time.sleep(0.5)
        time.sleep(1.0)  # let the final response reach the browser
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    swipes = gf.load_swipes(paths)
    print(f"Done. {len(swipes)} swipes recorded in total." + (" (demo, nothing saved)" if args.demo else ""))


if __name__ == "__main__":
    main()
