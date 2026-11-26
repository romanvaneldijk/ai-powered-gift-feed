import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import cli  # noqa: E402
import giftfeed as gf  # noqa: E402
import scout  # noqa: E402
import shops  # noqa: E402

SETTINGS = gf.build_settings(5, 25, 80, 200, big=1, medium=3, small=6, deck_size=10)

SHOPIFY = json.dumps({"resources": {"results": {"products": [
    {"title": "Moerheim blue keel", "url": "/products/moerheim-blue-keel?_pos=1&_sid=abc&_ss=r",
     "price": "89.00", "image": "https://bonneuniform.com/cdn/shop/files/keel.jpg", "available": True},
    {"title": "Brown classic suit", "url": "/products/brown-classic-suit?_pos=2",
     "price": "349.00", "featured_image": {"url": "//bonneuniform.com/cdn/shop/files/suit.jpg"}, "available": False},
]}}})

TILES = """<html><body>
<header><a href="/account/">Account</a><a href="/zoeken?q=x">Zoek</a><img src="/logo.svg"></header>
<ul class="results">
 <li class="tile"><a href="/koffie/hario-grinder-12345/"><img data-src="/img/grinder.jpg" alt="Hario grinder"></a>
   <a href="/koffie/hario-grinder-12345/" class="name">Hario handkoffiemolen&#8203;</a>
   <span class="old">€ 64,95</span> <span class="price">€ 54,95</span></li>
 <li class="tile"><a href="/koffie/v60-dripper-23456/"><img src="data:image/gif;base64,AAA" srcset="/img/v60.jpg 1x, /img/v60@2x.jpg 2x"></a>
   <a href="/koffie/v60-dripper-23456/">V60 dripper</a> <div data-price="12.50">12,50</div></li>
 <li class="tile"><a href="/koffie/moka-pot-34567/">Moka pot</a><span>€ 29,-</span></li>
</ul>
<script>var hidden = '<a href="/koffie/secret-99999/">Secret</a>';</script>
<footer><a href="/klantenservice/">Help</a></footer></body></html>"""

ITEMLIST = "<html><head>" + "".join(
    f'<script type="application/ld+json">{json.dumps(o)}</script>' for o in [{
        "@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": i, "item": {
                "@type": "Product", "name": f"Mug {i}", "url": f"https://shop.example/p/mug-{i}",
                "image": f"https://shop.example/mug-{i}.jpg",
                "offers": {"price": 10 + i, "priceCurrency": "EUR", "availability": "InStock"}}}
            for i in range(1, 5)]}]) + "</head><body></body></html>"

SOURCES = """# My gift sources

## Favourite websites
- https://www.bol.com/
- https://www.dille-kamille.nl/
- https://bonneuniform.com/
- https://www.cookinglife.nl/ (search: https://www.cookinglife.nl/search?term={q})
- https://www.unknown-shop.nl/

## Instagram gift guides
- https://www.instagram.com/whattowearfor_/

## LTK and other creator shops
- https://www.shopltk.com/explore/WhatToWearFor_/
"""


class ParseTests(unittest.TestCase):
    def test_shopify_suggest(self):
        res = shops.parse_shopify(SHOPIFY, "https://bonneuniform.com/search/suggest.json?q=x", "Bonne Uniform")
        self.assertEqual(res[0], {"title": "Moerheim blue keel", "price": 89.0, "in_stock": True,
                                  "image": "https://bonneuniform.com/cdn/shop/files/keel.jpg",
                                  "url": "https://bonneuniform.com/products/moerheim-blue-keel", "shop": "Bonne Uniform"})
        self.assertEqual(res[1]["image"], "https://bonneuniform.com/cdn/shop/files/suit.jpg")
        self.assertFalse(res[1]["in_stock"])
        self.assertEqual(shops.parse_shopify("<html>not json", "https://x.example/"), [])

    def test_html_tiles(self):
        res = shops.parse_html(TILES, "https://www.cookinglife.nl/zoeken?q=koffie", "Cookinglife")
        self.assertEqual([r["title"] for r in res], ["Hario handkoffiemolen", "V60 dripper", "Moka pot"])
        self.assertEqual([r["price"] for r in res], [54.95, 12.5, 29.0])
        self.assertEqual(res[0]["image"], "https://www.cookinglife.nl/img/grinder.jpg")
        self.assertEqual(res[1]["image"], "https://www.cookinglife.nl/img/v60.jpg")
        self.assertEqual(res[0]["url"], "https://www.cookinglife.nl/koffie/hario-grinder-12345/")

    def test_html_prefers_json_ld(self):
        res = shops.parse_html(ITEMLIST, "https://shop.example/search?q=mug")
        self.assertEqual(len(res), 4)
        self.assertEqual((res[0]["title"], res[0]["price"], res[0]["in_stock"]), ("Mug 1", 11.0, True))

    def test_recipes_from_sources(self):
        r = shops.recipes_from_sources(SOURCES)
        self.assertEqual(list(r), ["bol.com", "dille-kamille.nl", "bonneuniform.com", "cookinglife.nl", "unknown-shop.nl"])
        self.assertEqual(r["bonneuniform.com"]["kind"], "shopify")
        self.assertIsNone(r["bol.com"]["search"])
        self.assertEqual(r["cookinglife.nl"]["search"], "https://www.cookinglife.nl/search?term={q}")
        self.assertNotIn("search", r["unknown-shop.nl"])
        self.assertNotIn("instagram.com", r)
        self.assertNotIn("shopltk.com", r)

    def test_search_url(self):
        self.assertEqual(shops.search_url({"search": "https://s.example/?q={q}"}, " koffie & thee "),
                         "https://s.example/?q=koffie+%26+thee")


PAGES = {
    "/robots.txt": (200, "User-agent: *\nDisallow: /private/\n"),
    "/search?q=koffie": (200, TILES.replace("/koffie/", "/p/")),
    "/private/search?q=koffie": (200, TILES),
    "/p/hario-grinder-12345/": (200, '<html><head><script type="application/ld+json">' + json.dumps(
        {"@type": "Product", "name": "Hario handkoffiemolen", "image": "https://cdn.example/g.jpg",
         "offers": {"price": "54,95", "priceCurrency": "EUR", "availability": "InStock"}}) + "</script></head></html>"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/r/"):  # an affiliate redirect, like LTK's links to shops
            self.send_response(302)
            self.send_header("Location", self.path.split("?to=", 1)[1])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        code, body = PAGES.get(self.path, (404, "missing"))
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = gf.paths(self.tmp.name)
        gf.write_json(self.p.settings, SETTINGS)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv) -> list[str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--me", self.tmp.name, *argv])
        return out.getvalue().splitlines()

    def test_search_robots_blocked(self):
        recipe = {"name": "Local", "kind": "html", "search": self.base + "/private/search?q={q}"}
        res = shops.search_shop(scout.Fetcher(delay=0), "127.0.0.1", recipe, "koffie")
        self.assertEqual(res["status"], "blocked")

    def test_cli_search_and_check(self):
        self.p.sources.write_text(f"## Favourite websites\n- {self.base}/ (search: {self.base}/search?q={{q}})\n",
                                  encoding="utf-8")
        lines = self.run_cli("search", "koffie", "--check", "--check-max", "1", "--delay", "0")
        first = json.loads(lines[0])
        self.assertEqual(first["status"], "ok")
        self.assertEqual([r["tier"] for r in first["results"]], ["medium", "small", "medium"])
        checked = [json.loads(l) for l in lines if l.startswith("{") and '"status": "ok"' in l and "page" not in l]
        self.assertEqual(checked[0]["title"], "Hario handkoffiemolen")
        # The checked page is in the pool, so it can become a card by its URL.
        card = gf.add_card(self.p, {"url": checked[0]["url"], "tags": ["coffee"], "reason": "r", "kind": "fit"}, SETTINGS)
        self.assertEqual(card["price"], 54.95)
        # Once it is a card, the next search leaves it out.
        again = json.loads(self.run_cli("search", "koffie", "--delay", "0")[0])
        self.assertNotIn(checked[0]["url"], [r["url"] for r in again["results"]])

    def test_creator_page_link_through_redirect_becomes_card(self):
        shop = self.base + "/p/hario-grinder-12345/"
        redirect = self.base.replace("127.0.0.1", "localhost") + "/r/1?to=" + shop
        PAGES["/ltk"] = (200, f'<html><body><a href="{redirect}">Hario grinder</a>'
                              f'<a href="/explore/other-creator">Other</a></body></html>')
        links = json.loads(self.run_cli("links", "--any-shop", self.base + "/ltk", "--delay", "0")[0])["links"]
        self.assertEqual([l["url"] for l in links], [redirect])
        rows = [json.loads(l) for l in self.run_cli("check", redirect, "--delay", "0") if l.startswith("{")]
        self.assertEqual((rows[0]["status"], rows[0]["final_url"]), ("ok", shop))
        card = gf.add_card(self.p, {"url": rows[0]["final_url"], "tags": ["coffee"], "reason": "r", "kind": "fit",
                                    "source": self.base + "/ltk"}, SETTINGS)
        self.assertEqual((card["url"], card["price"]), (shop, 54.95))


class CrawlDelayTests(unittest.TestCase):
    def test_fetcher_honours_crawl_delay(self):
        import time

        class Fake(scout.Fetcher):
            def _raw_get(self, url):
                if url.endswith("/robots.txt"):
                    return 200, url, "User-agent: *\nCrawl-delay: 1\n"
                return 200, url, "<html></html>"

        f = Fake(delay=0)
        t = time.monotonic()
        f.get("https://slow.example/a")
        f.get("https://slow.example/b")
        self.assertGreaterEqual(time.monotonic() - t, 1.9)  # robots.txt, then a, then b, 1 s apart


if __name__ == "__main__":
    unittest.main()
