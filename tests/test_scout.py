import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import giftfeed as gf  # noqa: E402
import scout  # noqa: E402

SETTINGS = gf.build_settings(5, 25, 80, 200, big=1, medium=3, small=6, deck_size=10)


def ld(obj) -> str:
    return f'<script type="application/ld+json">{json.dumps(obj)}</script>'


JSONLD_PAGE = """<html><head><title>Grinder | Shop</title>
<meta property="og:image" content="/img/og.jpg"><meta property="og:site_name" content="Cookinglife">
""" + ld({"@context": "https://schema.org", "@graph": [
    {"@type": "BreadcrumbList", "itemListElement": []},
    {"@type": "Product", "name": "Hand coffee grinder", "image": ["https://cdn.example/grinder.jpg"],
     "brand": {"@type": "Brand", "name": "Hario"},
     "offers": {"@type": "Offer", "price": "54,95", "priceCurrency": "EUR",
                "availability": "https://schema.org/InStock"}},
]}) + "</head><body>x</body></html>"

OG_ONLY_PAGE = """<html><head>
<meta property="og:title" content="Linen apron &amp; pocket">
<meta property="og:image" content="https://cdn.example/apron.jpg">
<meta property="product:price:amount" content="29.00"><meta property="product:price:currency" content="EUR">
<meta property="product:availability" content="out of stock">
</head></html>"""

VARIANTS_PAGE = "<html><head>" + ld({
    "@type": "ProductGroup", "name": "Wool scarf", "image": "https://cdn.example/scarf.jpg",
    "hasVariant": [
        {"@type": "Product", "offers": {"price": 95, "priceCurrency": "EUR", "availability": "OutOfStock"}},
        {"@type": "Product", "offers": {"price": 89, "priceCurrency": "EUR", "availability": "InStock"}},
    ]}) + "</head></html>"

MICRODATA_PAGE = """<html><body><div itemscope itemtype="https://schema.org/Product">
<h1 itemprop="name">Candle</h1><img itemprop="image" src="/c.jpg">
<span itemprop="price" content="12.50"></span><link itemprop="availability" href="https://schema.org/InStock">
</div></body></html>"""

LISTING_PAGE = """<html><body>
<a href="/nl/nl/p/hario-grinder/9300000012345/">Hario grinder</a>
<a href="/nl/nl/p/hario-grinder/9300000012345/#reviews">reviews</a>
<a href="/winkelwagen/">cart</a><a href="/klantenservice/">help</a>
<a href="https://other.example/p/thing/1234567/">elsewhere</a>
<a href="/kookgerei/pannen/le-creuset-braadpan-24-cm.html">Le Creuset</a>
</body></html>"""

GUIDE_PAGE = """<html><body><a href="/about/">about</a>
<a href="https://www.instagram.com/someone/">insta</a>
<a href="https://shop.example/item/linen-apron">Linen apron</a></body></html>"""


class ParseTests(unittest.TestCase):
    def test_json_ld_in_graph(self):
        f = scout.extract_product(JSONLD_PAGE, "https://www.cookinglife.nl/grinder")
        self.assertEqual(f["title"], "Hand coffee grinder")
        self.assertEqual(f["price"], 54.95)
        self.assertEqual(f["currency"], "EUR")
        self.assertIs(f["in_stock"], True)
        self.assertEqual(f["image"], "https://cdn.example/grinder.jpg")
        self.assertEqual(f["shop"], "Cookinglife")

    def test_open_graph_only(self):
        f = scout.extract_product(OG_ONLY_PAGE, "https://www.dille-kamille.nl/apron")
        self.assertEqual(f["title"], "Linen apron & pocket")
        self.assertEqual(f["price"], 29.0)
        self.assertIs(f["in_stock"], False)
        self.assertEqual(f["shop"], "Dille Kamille")

    def test_product_group_prefers_in_stock_variant(self):
        f = scout.extract_product(VARIANTS_PAGE)
        self.assertEqual((f["price"], f["in_stock"]), (89.0, True))
        self.assertEqual(f["image"], "https://cdn.example/scarf.jpg")

    def test_microdata(self):
        f = scout.extract_product(MICRODATA_PAGE, "https://shop.example/candle")
        self.assertEqual((f["title"], f["price"], f["in_stock"]), ("Candle", 12.5, True))
        self.assertEqual(f["image"], "https://shop.example/c.jpg")

    def test_parse_price(self):
        for raw, want in [("54,95", 54.95), ("€ 1.299,00", 1299.0), ("1,299.00", 1299.0), ("1.299", 1299.0),
                          (" 19.5 ", 19.5), (25, 25.0), ("", None), ("gratis", None)]:
            self.assertEqual(scout.parse_price(raw), want, raw)

    def test_links_on_listing_page(self):
        links = scout.extract_links(LISTING_PAGE, "https://www.bol.com/nl/nl/s/?searchtext=koffie")
        self.assertEqual([l["url"] for l in links], [
            "https://www.bol.com/nl/nl/p/hario-grinder/9300000012345/",
            "https://www.bol.com/kookgerei/pannen/le-creuset-braadpan-24-cm.html",
        ])

    def test_links_on_guide_go_to_shops(self):
        links = scout.extract_links(GUIDE_PAGE, "https://blog.example/gift-guide", any_host=True)
        self.assertEqual([l["url"] for l in links], ["https://shop.example/item/linen-apron"])


PAGES = {
    "/grinder": (200, JSONLD_PAGE),
    "/apron": (200, OG_ONLY_PAGE),
    "/scarf": (200, VARIANTS_PAGE),
    "/blocked": (403, "nope"),
    "/captcha": (200, "<html>Please solve this captcha</html>"),
    "/gone": (404, "missing"),
    "/no-price": (200, "<html><head><title>Hello</title></head></html>"),
    "/private/secret": (200, JSONLD_PAGE),
    "/robots.txt": (200, "User-agent: *\nDisallow: /private/\n"),
}
for i in range(12):
    PAGES[f"/p/{i}"] = (200, "<html><head>" + ld({"@type": "Product", "name": f"Gift {i}", "image": "/i.jpg",
                                                  "offers": {"price": 10 + i, "availability": "InStock"}}) + "</head></html>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        time.sleep(0.2)  # a shop takes a moment to answer
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
        cls.port = cls.server.server_address[1]
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

    def url(self, path, host="127.0.0.1"):
        return f"http://{host}:{self.port}{path}"

    def test_check_statuses(self):
        paths = ["/grinder", "/apron", "/scarf", "/blocked", "/captcha", "/gone", "/no-price", "/private/secret"]
        res = scout.check_many([self.url(x) for x in paths], scout.Fetcher(delay=0))
        self.assertEqual([r["status"] for r in res],
                         ["ok", "out-of-stock", "ok", "blocked", "blocked", "gone", "no-product", "blocked"])

    def test_check_then_add_card_by_url(self):
        url = self.url("/grinder")
        res = gf.save_checked(self.p, scout.check_many([url, self.url("/apron")], scout.Fetcher(delay=0)), SETTINGS)
        self.assertEqual(res[0]["tier"], "medium")
        card = gf.add_card(self.p, {"url": url, "tags": ["coffee"], "reason": "r", "kind": "fit"}, SETTINGS)
        self.assertEqual((card["title"], card["price"], card["shop"]), ("Hand coffee grinder", 54.95, "Cookinglife"))
        self.assertEqual(card["image"], "https://cdn.example/grinder.jpg")
        # Now a card, so it leaves the pool and shows as known.
        self.assertEqual(gf.pool_candidates(self.p, SETTINGS), [])
        again = gf.save_checked(self.p, scout.check_many([url], scout.Fetcher(delay=0)), SETTINGS)
        self.assertTrue(again[0]["known"])

    def test_stale_pool_entry_is_refused(self):
        url = self.url("/grinder")
        gf.save_checked(self.p, scout.check_many([url], scout.Fetcher(delay=0)), SETTINGS)
        pool = gf.load_pool(self.p)
        for e in pool.values():
            e["checked_at"] = "2020-01-01T00:00:00+00:00"
        gf.write_json(self.p.pool, pool)
        with self.assertRaises(ValueError):
            gf.add_card(self.p, {"url": url, "tags": ["coffee"]}, SETTINGS)



class FakeShopFetcher(scout.Fetcher):
    """Every host answers after 0.2 s, without touching the network."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.active: dict[str, int] = {}
        self.overlap = False

    def _raw_get(self, url):
        host = url.split("/")[2]
        self.active[host] = self.active.get(host, 0) + 1
        self.overlap |= self.active[host] > 1
        time.sleep(0.2)
        self.active[host] -= 1
        if url.endswith("/robots.txt"):
            return 404, url, ""
        return 200, url, PAGES["/p/1"][1]


class SpeedTests(unittest.TestCase):
    def test_one_page_at_a_time_per_shop_but_shops_in_parallel(self):
        urls = [f"https://shop{i % 4}.example/p/{i}" for i in range(12)]
        fetcher = FakeShopFetcher(delay=0.5)
        t = time.monotonic()
        res = scout.check_many(urls, fetcher, workers=6)
        took = time.monotonic() - t
        self.assertTrue(all(r["status"] == "ok" for r in res), res)
        self.assertFalse(fetcher.overlap, "two pages of one shop were read at the same time")
        # Per shop: robots.txt + 3 pages, with 0.5 s pauses: about 2.3 s. One by one: 12 x 0.7 s or more.
        self.assertLess(took, 4.0)
        self.assertGreater(took, 1.9)


if __name__ == "__main__":
    unittest.main()
