"""Shop search helpers for /scout: search a shop with its own search page, without Claude.

Each shop gets a small recipe: the address of its search page, and how to read the results.
Two kinds of recipe exist:

- `shopify`: shops built on Shopify answer `/search/suggest.json?q=…` with clean JSON
  (name, price, photo, link, stock). This is the most reliable kind.
- `html`: an ordinary search page. The reader looks for product data in JSON-LD first, then
  cuts the page into one piece per product link and reads the name, price and photo from
  each piece, so one odd product tile cannot spoil the rest.

The built-in recipes cover the example shops. A user adds their own with a `search:` address
in `me/sources.md`, for example `- https://www.cookinglife.nl/ (search: https://www.cookinglife.nl/zoeken?q={q})`.

Search results are only leads: the prices and photos on a results page can be out of date.
A card still comes from the product page itself, read by `cli.py check`. Every request goes
through `scout.Fetcher`, so robots.txt is honoured and a shop is never asked twice at once.
Standard library only.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlsplit

import scout

# Host (without www.) -> recipe. "checked" says when the recipe was last seen working.
RECIPES: dict[str, dict] = {
    # Shopify shops: the suggest.json search is public, and both shops' robots.txt allow it.
    "bonneuniform.com": {
        "name": "Bonne Uniform",
        "kind": "shopify",
        "search": "https://bonneuniform.com/search/suggest.json?q={q}&resources[type]=product&resources[limit]=10",
    },
    "cookinglife.nl": {
        "name": "Cookinglife",
        "kind": "shopify",
        "search": "https://www.cookinglife.nl/search/suggest.json?q={q}&resources[type]=product&resources[limit]=10",
    },
    # Magento shop with "Crawl-delay: 10", which the Fetcher honours. Its robots.txt disallows the
    # standard /catalogsearch/ path; this Dutch search address is a best guess, not yet confirmed.
    "dille-kamille.nl": {
        "name": "Dille & Kamille",
        "kind": "html",
        "search": "https://www.dille-kamille.nl/zoeken/?q={q}",
    },
    # bol.com's robots.txt has "Disallow: /*/s/", which is its search page. So no recipe:
    # /scout uses a web search (site:bol.com …) for bol.com instead.
    "bol.com": {
        "name": "bol.com",
        "search": None,
        "note": "bol.com asks scripts not to read its search pages: use a web search",
    },
}

INVISIBLE = re.compile("[\u0000-\u0008\u000b-\u001f\u007f-\u009f​-‏‪-‮⁠-⁯︀-️﻿]"
                       "|[\U000e0000-\U000e007f]")
PRICE_TEXT = re.compile(r"€\s*(\d{1,5}(?:[.\s]\d{3})*(?:[.,]\d{1,2}|,-)?)|(\d{1,5}(?:[.,]\d{2}))\s*€")
PRICE_ATTR = re.compile(r"""(?:data-price(?:-amount)?|itemprop=["']price["'][^>]*?content)\s*=\s*["']([\d.,]+)["']""", re.I)
TAG = re.compile(r"<[^>]+>")
HIDDEN_BLOCK = re.compile(r"<(script|style|template|noscript)\b.*?</\1\s*>|<!--.*?-->", re.I | re.S)
ANCHOR = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"']+)["'][^>]*>""", re.I)
IMG = re.compile(r"<img\b[^>]*>", re.I)
ATTR = re.compile(r"""([\w:-]+)\s*=\s*["']([^"']*)["']""")


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def clean(text) -> str:
    """Visible, single-line text: entities decoded, invisible characters removed."""
    return INVISIBLE.sub("", re.sub(r"\s+", " ", unescape(str(text or "")))).strip()


# ---------------------------------------------------------------- recipes from sources.md


def recipes_from_sources(text: str) -> dict[str, dict]:
    """The user's favourite websites, each with a recipe if one is known.

    A `search:` address in sources.md wins over the built-in recipe for that shop."""
    out: dict[str, dict] = {}
    section = ""
    for line in text.splitlines():
        if line.startswith("#"):
            section = line.lower()
            continue
        if section and not ("favourite" in section or "website" in section):
            continue  # Instagram and creator-shop lists are gift guides, not shops
        m = re.match(r"\s*[-*]\s*(https?://\S+?)\s*(?:\((?:search:\s*)?(https?://[^)\s]+)\))?\s*$", line)
        if not m:
            continue
        site, search = m.group(1), m.group(2)
        host = host_of(site)
        recipe = dict(RECIPES.get(host, {}))
        if search and "{q}" in search:
            recipe.update(search=search, kind="shopify" if "/search/suggest.json" in search else "html")
        recipe.setdefault("name", scout.shop_from_url(site))
        out[host] = recipe
    return out


def search_url(recipe: dict, query: str) -> str:
    return recipe["search"].replace("{q}", quote_plus(query.strip()))


# ---------------------------------------------------------------- reading results


def parse_shopify(body: str, base_url: str, shop: str | None = None) -> list[dict]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    products = (((data or {}).get("resources") or {}).get("results") or {}).get("products") or []
    out = []
    for p in products:
        if not isinstance(p, dict) or not p.get("url"):
            continue
        price = p.get("price")
        price = price / 100 if isinstance(price, int) and not isinstance(price, bool) else scout.parse_price(price)
        image = p.get("image")
        if not image and isinstance(p.get("featured_image"), dict):
            image = p["featured_image"].get("url")
        if isinstance(image, str) and image.startswith("//"):
            image = "https:" + image
        url = urljoin(base_url, p["url"]).split("?")[0]
        out.append({"title": clean(p.get("title")), "price": price, "image": image or None, "url": url,
                    "in_stock": p["available"] if isinstance(p.get("available"), bool) else None, "shop": shop})
    return out


def _jsonld_results(html: str, base_url: str, shop: str | None) -> list[dict]:
    page = scout.parse(html)
    out = []
    for obj in scout._jsonld_objects(page.jsonld):
        types = scout._types(obj)
        item = obj.get("item") if "listitem" in types and isinstance(obj.get("item"), dict) else obj
        if not scout._types(item) & {"product", "productgroup"}:
            continue
        url = item.get("url") or item.get("@id") or (obj.get("url") if item is not obj else None)
        if not isinstance(url, str) or not url.strip():
            continue
        price, currency, stock = scout._offer_facts(item.get("offers"))
        if currency and str(currency).upper() != "EUR":
            continue
        out.append({"title": clean(item.get("name")), "price": price, "image": scout._image(item.get("image")),
                    "url": urljoin(base_url, url.strip()).split("#")[0], "in_stock": stock, "shop": shop})
    return out


def _first_image(chunk: str, base_url: str) -> tuple[str | None, str]:
    for tag in IMG.findall(chunk):
        a = {k.lower(): unescape(v) for k, v in ATTR.findall(tag)}
        src = ""
        for key in ("data-src", "data-original", "data-lazy-src", "src", "data-srcset", "srcset"):
            v = a.get(key, "").strip()
            if v and not v.startswith("data:"):
                src = v.split(",")[0].strip().split(" ")[0]
                break
        if not src or re.search(r"(logo|icon|sprite|placeholder|spinner)[^/]*$|\.svg(\?|$)", src, re.I):
            continue
        return urljoin(base_url, src), clean(a.get("alt"))
    return None, ""


def _chunk_price(chunk: str) -> float | None:
    attr = [scout.parse_price(v) for v in PRICE_ATTR.findall(chunk)]
    attr = [p for p in attr if p]
    if attr:
        return min(attr)
    text = clean(TAG.sub(" ", chunk))
    found = [scout.parse_price((a or b).replace(",-", ",00").replace(" ", "")) for a, b in PRICE_TEXT.findall(text)]
    found = [p for p in found if p]
    return min(found) if found else None  # a sale price is the lower of two


def parse_html(html: str, base_url: str, shop: str | None = None, limit: int = 20) -> list[dict]:
    """Products on a search results page: JSON-LD first, then one chunk per product tile."""
    results = {r["url"]: r for r in _jsonld_results(html, base_url, shop)}
    if len(results) >= 3:
        return list(results.values())[:limit]

    visible = HIDDEN_BLOCK.sub(" ", html)
    wanted = {l["url"]: l["text"] for l in scout.extract_links(visible, base_url, limit=limit * 3)}
    first: dict[str, int] = {}
    texts: dict[str, list[str]] = {}
    for m in ANCHOR.finditer(visible):
        url = urljoin(base_url, unescape(m.group(1).strip())).split("#")[0]
        if url not in wanted:
            continue
        first.setdefault(url, m.start())
        end = visible.find("</a>", m.end())
        texts.setdefault(url, []).append(clean(TAG.sub(" ", visible[m.end():end if end > 0 else m.end()])))
    order = sorted(first, key=first.get)
    for i, url in enumerate(order):
        if url in results:
            continue
        stop = first[order[i + 1]] if i + 1 < len(order) else len(visible)
        chunk = visible[first[url]:min(stop, first[url] + 8000)]
        image, alt = _first_image(chunk, base_url)
        names = sorted((t for t in texts.get(url, []) if t and not PRICE_TEXT.search(t)), key=len, reverse=True)
        title = names[0] if names else alt or wanted[url]
        results[url] = {"title": title[:120], "price": _chunk_price(chunk), "image": image, "url": url,
                        "in_stock": None, "shop": shop}
    return [r for r in results.values() if r["title"]][:limit]


# ---------------------------------------------------------------- searching


def search_shop(fetcher: scout.Fetcher, host: str, recipe: dict, query: str, limit: int = 10) -> dict:
    url = search_url(recipe, query)
    base = {"shop": recipe.get("name") or host, "host": host, "query": query, "page": url}
    res = fetcher.get(url)
    if res["error"]:
        status = "blocked" if "robots" in res["error"] else "error"
        return base | {"status": status, "note": res["error"], "results": []}
    if scout.looks_blocked(res["status"], res["html"]):
        return base | {"status": "blocked", "note": f"HTTP {res['status']}, the shop blocks scripts", "results": []}
    if res["status"] >= 400:
        return base | {"status": "error", "note": f"HTTP {res['status']}", "results": []}
    body = res["html"] or ""
    if recipe.get("kind") == "shopify":
        found = parse_shopify(body, res["url"], base["shop"])
    else:
        found = parse_html(body, res["url"], base["shop"], limit=limit * 2)
    found = [r for r in found if host_of(r["url"]) == host or host_of(r["url"]).endswith("." + host)][:limit]
    return base | {"status": "ok" if found else "no-results", "results": found}


def search_many(jobs: list[tuple[str, dict, str]], fetcher: scout.Fetcher | None = None,
                limit: int = 10, workers: int = scout.WORKERS) -> list[dict]:
    """Run (host, recipe, query) searches: shops in parallel, one page at a time per shop."""
    fetcher = fetcher or scout.Fetcher()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda j: search_shop(fetcher, j[0], j[1], j[2], limit), jobs))


def load_recipes(sources: Path) -> dict[str, dict]:
    text = sources.read_text(encoding="utf-8") if sources.exists() else ""
    return recipes_from_sources(text)
