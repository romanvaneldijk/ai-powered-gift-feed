"""Fast page reading for /scout: fetch product pages in parallel and pull out the facts.

Shops put their product facts in machine-readable tags for search engines: JSON-LD
(`schema.org/Product`), OpenGraph meta tags and microdata. Reading those with plain Python is
much faster and cheaper than having Claude read each page, so /scout uses this first and only
falls back to Claude's own page reading for pages this cannot read (blocked or no product data).

Polite by design: one request at a time per shop with a pause in between (longer if the shop's
robots.txt sets a Crawl-delay), robots.txt is honoured,
and different shops are read in parallel. Standard library only.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
import zlib
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

USER_AGENT = "Mozilla/5.0 (compatible; gift-feed/1.0; personal use; +https://github.com/romanvaneldijk/ai-powered-gift-feed)"
TIMEOUT = 15
MAX_BYTES = 4_000_000
PER_HOST_DELAY = 1.0  # seconds between two requests to the same shop
WORKERS = 6  # shops read at the same time

IN_STOCK = ("instock", "in stock", "limitedavailability", "onlineonly", "in_stock", "available")
OUT_OF_STOCK = ("outofstock", "out of stock", "soldout", "sold out", "discontinued", "out_of_stock",
                "instoreonly", "preorder", "presale", "backorder")
BLOCK_HINTS = ("captcha", "cf-challenge", "challenge-platform", "access denied", "are you a robot",
               "unusual traffic", "px-captcha", "datadome")


# ---------------------------------------------------------------- HTML parsing


class _PageParser(HTMLParser):
    """Collects meta tags, JSON-LD blocks, microdata, links and the <title> in one pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.jsonld: list[str] = []
        self.itemprops: dict[str, str] = {}
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._in_jsonld = False
        self._in_title = False
        self._buf: list[str] = []
        self._link: str | None = None
        self._link_text: list[str] = []
        self._prop: tuple[str, str] | None = None  # (tag, itemprop) whose text is being read
        self._prop_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_jsonld, self._buf = True, []
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and a.get("href"):
            self._link, self._link_text = a["href"], []
        if "itemprop" in a and tag != "meta":
            prop = a["itemprop"].lower()
            value = a.get("content") or a.get("href") or a.get("src") or ""
            if value and prop not in self.itemprops:
                self.itemprops[prop] = value.strip()
            elif not value and prop not in self.itemprops and self._prop is None:
                self._prop, self._prop_text = (tag, prop), []
        if tag == "link" and "itemprop" in a and a.get("href"):
            self.itemprops.setdefault(a["itemprop"].lower(), a["href"])

    def handle_endtag(self, tag):
        if self._prop and tag == self._prop[0]:
            text = re.sub(r"\s+", " ", "".join(self._prop_text)).strip()
            if text:
                self.itemprops.setdefault(self._prop[1], text)
            self._prop = None
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._buf))
            self._in_jsonld = False
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._link is not None:
            text = re.sub(r"\s+", " ", "".join(self._link_text)).strip()
            self.links.append((self._link, text))
            self._link = None

    def handle_data(self, data):
        if self._in_jsonld:
            self._buf.append(data)
        elif self._in_title:
            self.title += data
        if self._link is not None:
            self._link_text.append(data)
        if self._prop is not None:
            self._prop_text.append(data)


def parse(html: str) -> _PageParser:
    p = _PageParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # broken HTML: keep whatever was parsed
        pass
    return p


def _jsonld_objects(blocks: list[str]):
    """Yield every dict inside the JSON-LD blocks, including @graph members and nested ones."""
    def walk(node):
        if isinstance(node, dict):
            yield node
            for v in node.values():
                if isinstance(v, (dict, list)):
                    yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    for raw in blocks:
        raw = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            data = json.loads(raw, strict=False)
        except json.JSONDecodeError:
            try:  # trailing commas are a common mistake
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw), strict=False)
            except json.JSONDecodeError:
                continue
        yield from walk(data)


def _types(obj: dict) -> set[str]:
    t = obj.get("@type") or []
    return {str(x).split("/")[-1].lower() for x in (t if isinstance(t, list) else [t])}


def parse_price(value) -> float | None:
    """Read a price written as 54.95, "54,95", "€ 1.299,00" or "1,299.00"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d.,]", "", str(value))
    if not s:
        return None
    if "," in s and "." in s:
        # The last separator is the decimal one.
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = f"{head.replace(',', '')}.{tail}" if len(tail) <= 2 else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.rpartition(".")[2]) == 3 and len(s) > 4):
        s = s.replace(".", "")  # 1.299 is a thousands separator, not a decimal point
    try:
        return float(s)
    except ValueError:
        return None


def stock_from(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    v = str(value).lower().rsplit("/", 1)[-1].strip()
    if any(h in v for h in OUT_OF_STOCK):
        return False
    if any(h in v for h in IN_STOCK):
        return True
    return None


def _image(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return str(value).strip() if value else None


def _offer_facts(offers) -> tuple[float | None, str | None, bool | None]:
    if isinstance(offers, list):
        found = [_offer_facts(o) for o in offers]
        priced = [f for f in found if f[0] is not None]
        # Prefer an offer that is in stock, then the cheapest.
        priced.sort(key=lambda f: (f[2] is not True, f[0]))
        return priced[0] if priced else (None, None, None)
    if not isinstance(offers, dict):
        return None, None, None
    price = parse_price(offers.get("price"))
    if price is None:
        price = parse_price(offers.get("lowPrice"))
    if price is None and isinstance(offers.get("priceSpecification"), (dict, list)):
        spec = offers["priceSpecification"]
        spec = spec[0] if isinstance(spec, list) and spec else spec
        if isinstance(spec, dict):
            price = parse_price(spec.get("price"))
    stock = stock_from(offers.get("availability"))
    if price is None and isinstance(offers.get("offers"), (dict, list)):  # AggregateOffer
        inner = _offer_facts(offers["offers"])
        return inner[0], inner[1] or offers.get("priceCurrency"), inner[2] if stock is None else stock
    return price, offers.get("priceCurrency"), stock


def extract_product(html: str, url: str = "") -> dict:
    """Return the product facts on a page. Missing facts are None."""
    page = parse(html)
    out: dict = {"title": None, "price": None, "currency": None, "in_stock": None, "image": None,
                 "shop": None, "found_in": None}
    for obj in _jsonld_objects(page.jsonld):
        types = _types(obj)
        if not types & {"product", "productgroup", "individualproduct", "productmodel"}:
            continue
        price, currency, stock = _offer_facts(obj.get("offers"))
        if price is None and isinstance(obj.get("hasVariant"), list):
            variants = [v for v in obj["hasVariant"] if isinstance(v, dict)]
            facts = [_offer_facts(v.get("offers")) for v in variants]
            facts = [f for f in facts if f[0] is not None]
            facts.sort(key=lambda f: (f[2] is not True, f[0]))
            if facts:
                price, currency, stock = facts[0]
            if not obj.get("image") and variants:
                obj = {**obj, "image": variants[0].get("image")}
        if price is None and out["found_in"]:
            continue  # keep the first product that has a price
        out.update(title=obj.get("name"), price=price, currency=currency, in_stock=stock,
                   image=_image(obj.get("image")), found_in="json-ld")
        brand = obj.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        out["brand"] = brand if isinstance(brand, str) else None
        if price is not None:
            break

    m, ip = page.meta, page.itemprops
    out["title"] = out["title"] or m.get("og:title") or ip.get("name") or page.title.strip() or None
    out["image"] = out["image"] or m.get("og:image") or m.get("og:image:secure_url") or m.get("twitter:image") or ip.get("image")
    if out["price"] is None:
        for key in ("product:price:amount", "og:price:amount", "product:sale_price:amount", "twitter:data1"):
            if parse_price(m.get(key)) is not None:
                out["price"] = parse_price(m[key])
                out["currency"] = m.get(key.replace("amount", "currency")) or out["currency"]
                out["found_in"] = out["found_in"] or "meta"
                break
    if out["price"] is None and parse_price(ip.get("price")) is not None:
        out["price"] = parse_price(ip["price"])
        out["currency"] = out["currency"] or ip.get("pricecurrency")
        out["found_in"] = out["found_in"] or "microdata"
    if out["in_stock"] is None:
        out["in_stock"] = stock_from(m.get("product:availability") or m.get("og:availability") or ip.get("availability"))
    out["shop"] = m.get("og:site_name") or shop_from_url(url)
    if out["image"] and url:
        out["image"] = urljoin(url, out["image"])
    if out["title"]:
        out["title"] = unescape(re.sub(r"\s+", " ", str(out["title"]))).strip()
    if isinstance(out["currency"], str):
        out["currency"] = out["currency"].strip().upper() or None
    return out


def shop_from_url(url: str) -> str | None:
    host = urlsplit(url).hostname or ""
    host = re.sub(r"^(www\d?|shop|m)\.", "", host)
    name = host.split(".")[0] if host else ""
    return name.replace("-", " ").title() if name else None


# ---------------------------------------------------------------- links on listing pages

PRODUCT_PATH = re.compile(
    r"/(p|product|products|producten|artikel|item|items|shop/[^/]+)/|\.html?$|[-/]\d{5,}(/|$|\.)|/[a-z0-9-]+-\d{3,}/?$",
    re.I,
)
SKIP_PATH = re.compile(
    r"/(cart|winkelwagen|checkout|account|login|inloggen|register|wishlist|verlanglijst|klantenservice|"
    r"customer-service|contact|faq|help|blog|privacy|cookie|voorwaarden|terms|retour|returns|vacatures|"
    r"careers|over-ons|about|stores|winkels|nieuwsbrief|newsletter|search|zoeken|s)(/|$)",
    re.I,
)


def extract_links(html: str, base_url: str, any_host: bool = False, limit: int = 40) -> list[dict]:
    """Product links on a search, category or gift-guide page, best guesses first."""
    page = parse(html)
    base_host = (urlsplit(base_url).hostname or "").removeprefix("www.")
    seen: set[str] = set()
    found: list[dict] = []

    def add(href: str, text: str, listed: bool) -> None:
        url = urljoin(base_url, unescape(href.strip())).split("#")[0]
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return
        host = (parts.hostname or "").removeprefix("www.")
        if not any_host and host != base_host:
            return
        if any_host and host == base_host and not listed:
            return  # on a guide, the links worth following point to shops
        if host in ("instagram.com", "facebook.com", "pinterest.com", "x.com", "twitter.com", "tiktok.com", "youtube.com"):
            return
        key = url.rstrip("/").lower()
        if key in seen or key == base_url.rstrip("/").lower():
            return
        if not listed and (SKIP_PATH.search(parts.path) or not (PRODUCT_PATH.search(parts.path) or any_host)):
            return
        seen.add(key)
        found.append({"url": url, "text": text[:90]})

    for obj in _jsonld_objects(page.jsonld):  # ItemList on category and search pages
        if "listitem" in _types(obj) or "product" in _types(obj):
            href = obj.get("url") or (obj.get("item") if isinstance(obj.get("item"), str) else None)
            if not href and isinstance(obj.get("item"), dict):
                href = obj["item"].get("url") or obj["item"].get("@id")
            if isinstance(href, str):
                add(href, str(obj.get("name") or ""), listed=True)
    for href, text in page.links:
        add(href, text, listed=False)
    return found[:limit]


# ---------------------------------------------------------------- fetching


class Fetcher:
    """Reads pages politely: in parallel across shops, one at a time per shop, robots.txt honoured."""

    def __init__(self, delay: float = PER_HOST_DELAY, timeout: float = TIMEOUT, robots: bool = True):
        self.delay, self.timeout, self.robots = delay, timeout, robots
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._crawl_delay: dict[str, float] = {}  # a shop's own "Crawl-delay" in robots.txt
        self._guard = threading.Lock()

    def _host_lock(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def _wait_turn(self, host: str) -> None:
        if host in self._last:
            wait = self._last[host] + max(self.delay, self._crawl_delay.get(host, 0)) - time.monotonic()
            if wait > 0:
                time.sleep(wait)

    def _raw_get(self, url: str) -> tuple[int, str, str]:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read(MAX_BYTES)
                enc = (r.headers.get("Content-Encoding") or "").lower()
                status, final, charset = r.status, r.geturl(), r.headers.get_content_charset() or "utf-8"
        except urllib.error.HTTPError as e:
            return e.code, url, ""
        if enc == "gzip":
            body = gzip.decompress(body)
        elif enc == "deflate":
            body = zlib.decompress(body, -zlib.MAX_WBITS) if body[:1] != b"x" else zlib.decompress(body)
        return status, final, body.decode(charset, errors="replace")

    def _allowed(self, url: str) -> bool:
        if not self.robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                status, _, text = self._raw_get(origin + "/robots.txt")
                if status == 200:
                    rp.parse(text.splitlines())
                else:
                    rp = None  # no robots.txt (or unreadable): allowed
            except Exception:
                rp = None
            self._robots[origin] = rp
            try:
                cd = rp.crawl_delay(USER_AGENT) if rp else None
            except (TypeError, ValueError):
                cd = None
            if cd:
                self._crawl_delay[(parts.hostname or "").lower()] = min(float(cd), 30.0)
            self._last[(parts.hostname or "").lower()] = time.monotonic()
        rp = self._robots[origin]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def get(self, url: str) -> dict:
        """Return {"status", "url", "html"|None, "error"|None}. Status is the HTTP code or 0."""
        host = (urlsplit(url).hostname or "").lower()
        with self._host_lock(host):
            try:
                if self.robots and _origin(url) not in self._robots:
                    self._wait_turn(host)
                if not self._allowed(url):  # reading robots.txt counts as a visit too
                    return {"status": 0, "url": url, "html": None, "error": "robots.txt asks scripts not to read this page"}
                self._wait_turn(host)
                status, final, html = self._raw_get(url)
                return {"status": status, "url": final, "html": html, "error": None}
            except Exception as e:  # timeouts, DNS, TLS
                return {"status": 0, "url": url, "html": None, "error": f"{type(e).__name__}: {e}"[:160]}
            finally:
                self._last[host] = time.monotonic()  # the pause counts from the end of a visit


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def looks_blocked(status: int, html: str | None) -> bool:
    if status in (401, 403, 429, 503):
        return True
    if html and len(html) < 60_000:
        low = html.lower()
        return any(h in low for h in BLOCK_HINTS)
    return False


def check_page(fetcher: Fetcher, url: str) -> dict:
    """Fetch one product page and say what it holds. status is one of:
    ok, out-of-stock, no-product, blocked, gone, error."""
    res = fetcher.get(url)
    base = {"url": url}
    if res["error"]:
        kind = "blocked" if "robots.txt" in res["error"] else "error"
        return base | {"status": kind, "note": res["error"]}
    if res["status"] in (404, 410):
        return base | {"status": "gone", "note": f"HTTP {res['status']}"}
    facts = extract_product(res["html"] or "", res["url"]) if res["status"] < 400 else None
    if (facts is None or facts["price"] is None) and looks_blocked(res["status"], res["html"]):
        return base | {"status": "blocked", "note": f"HTTP {res['status']}, the shop blocks scripts"}
    if facts is None:
        return base | {"status": "error", "note": f"HTTP {res['status']}"}
    if facts["price"] is None and "/products/" in urlsplit(url).path:
        facts = _shopify_fallback(fetcher, url, facts)
    final = res["url"] if res["url"] != url else None
    out = base | facts
    if final:
        out["final_url"] = final
    if facts["price"] is None or not facts["title"]:
        return out | {"status": "no-product", "note": "no machine-readable price on this page"}
    if facts["in_stock"] is False:
        return out | {"status": "out-of-stock"}
    return out | {"status": "ok"}


def _shopify_fallback(fetcher: Fetcher, url: str, facts: dict) -> dict:
    """Shopify shops publish every product as JSON at <product url>.js."""
    res = fetcher.get(url.split("?")[0].rstrip("/") + ".js")
    if res["error"] or res["status"] != 200 or not res["html"]:
        return facts
    try:
        data = json.loads(res["html"])
    except json.JSONDecodeError:
        return facts
    price = data.get("price")
    image = data.get("featured_image") or ""
    return facts | {
        "title": facts["title"] or data.get("title"),
        "price": price / 100 if isinstance(price, (int, float)) else facts["price"],
        "in_stock": data.get("available") if isinstance(data.get("available"), bool) else facts["in_stock"],
        "image": facts["image"] or (("https:" + image) if image.startswith("//") else image) or None,
        "found_in": "shopify",
    }


def check_many(urls: list[str], fetcher: Fetcher | None = None, workers: int = WORKERS) -> list[dict]:
    """Check product pages in parallel across shops; results keep the input order."""
    fetcher = fetcher or Fetcher()
    urls = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda u: check_page(fetcher, u), urls))


def links_many(urls: list[str], any_host: bool = False, limit: int = 40,
               fetcher: Fetcher | None = None, workers: int = WORKERS) -> list[dict]:
    fetcher = fetcher or Fetcher()

    def one(url: str) -> dict:
        res = fetcher.get(url)
        if res["error"]:
            return {"page": url, "status": "blocked" if "robots" in res["error"] else "error", "note": res["error"], "links": []}
        if looks_blocked(res["status"], res["html"]) or res["status"] >= 400:
            return {"page": url, "status": "blocked" if looks_blocked(res["status"], res["html"]) else "error",
                    "note": f"HTTP {res['status']}", "links": []}
        links = extract_links(res["html"] or "", res["url"], any_host=any_host, limit=limit)
        return {"page": url, "status": "ok", "links": links}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, list(dict.fromkeys(urls))))
