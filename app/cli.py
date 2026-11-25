"""Command-line helpers that the Claude Code commands call.

Usage: python3 app/cli.py <command> [options]   (run `python3 app/cli.py -h` for the list)
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import giftfeed as gf  # noqa: E402
import scout  # noqa: E402


def cmd_init(args, p: gf.Paths) -> None:
    """Create me/ from the templates without overwriting anything."""
    p.me.mkdir(parents=True, exist_ok=True)
    p.feed.mkdir(exist_ok=True)
    for name in ("profile.md", "sources.md"):
        target = p.me / name
        if not target.exists():
            shutil.copy(gf.ROOT / "templates" / name, target)
            print(f"created {target.relative_to(gf.ROOT) if target.is_relative_to(gf.ROOT) else target}")
    print("me/ is ready")


def cmd_settings(args, p: gf.Paths) -> None:
    s = gf.build_settings(args.min_price, args.small_max, args.big_min, args.big_max,
                          args.big, args.medium, args.small, args.deck_size)
    old = gf.read_json(p.settings, {})
    for key in ("explore_share", "wildcard_share"):
        if key in old:
            s[key] = old[key]
    gf.write_json(p.settings, s)
    print(json.dumps(s, indent=2))


def cmd_add_card(args, p: gf.Paths) -> None:
    """Read one card or a list of cards as JSON from stdin or --file."""
    settings = gf.load_settings(p)
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    data = json.loads(text)
    raws = data if isinstance(data, list) else [data]
    ok, failed = 0, 0
    for raw in raws:
        try:
            card = gf.add_card(p, raw, settings)
            ok += 1
            label = card.get("title") or card.get("question")
            extra = f" [{card['tier']}, €{card['price']:.2f}]" if card["type"] == "gift" else " [question]"
            print(f"added {card['id']}: {label}{extra}")
        except ValueError as e:
            failed += 1
            print(f"skipped {raw.get('title') or raw.get('question') or raw}: {e}")
    print(f"{ok} added, {failed} skipped")


def cmd_status(args, p: gf.Paths) -> None:
    """What /scout needs to know: unseen cards per tier, today's quota and known product URLs."""
    settings = gf.load_settings(p)
    cards = gf.load_cards(p)
    # --next-deck: the deck on screen right now is already spoken for, so scout for the one after.
    taken = {c["id"] for c in gf.build_deck(settings, cards)} if args.next_deck else set()
    unseen = {t: 0 for t in gf.TIERS}
    for c in cards:
        if c["status"] == "new" and c.get("type") == "gift" and c["id"] not in taken:
            unseen[c["tier"]] += 1
    quota = gf.tier_quota(settings, cards)
    have = gf.wishlist_counts(cards)
    to_find = {t: max(quota[t] - unseen[t], 0) for t in gf.TIERS}
    out = {
        "price_tiers": settings["tiers"],
        "wishlist": {t: f"{have[t]}/{settings['targets'][t]}" for t in gf.TIERS},
        "next_deck_quota": quota,
        "unseen_cards": unseen,
        "cards_to_find": to_find,
        "unanswered_questions": sum(1 for c in cards if c.get("type") == "question" and c["status"] == "new"),
        "checked_products_waiting": dict(sorted(
            Counter(e["tier"] for e in gf.pool_candidates(p, settings)).items())),
    }
    if args.urls:
        out["known_urls"] = sorted(c["url"] for c in cards if c.get("url"))
    print(json.dumps(out, indent=2))


def _read_urls(args) -> list[str]:
    urls = list(args.urls)
    if args.file:
        urls += Path(args.file).read_text(encoding="utf-8").split()
    if not urls:
        raise SystemExit("give one or more URLs, or --file with one URL per line")
    return urls


def cmd_check(args, p: gf.Paths) -> None:
    """Read product pages in parallel and print their facts, one JSON line per page."""
    settings = gf.load_settings(p)
    started = time.monotonic()
    fetcher = scout.Fetcher(delay=args.delay)
    results = gf.save_checked(p, scout.check_many(_read_urls(args), fetcher, workers=args.workers), settings)
    keep = ("url", "status", "title", "shop", "price", "tier", "in_stock", "image", "known", "note", "final_url")
    for r in results:
        row = {k: r[k] for k in keep if r.get(k) not in (None, "")}
        if not row.get("known"):
            row.pop("known", None)
        print(json.dumps(row, ensure_ascii=False))
    counts = Counter(r["status"] for r in results)
    usable = sum(1 for r in results if r["status"] == "ok" and r.get("tier") and r.get("in_stock") and not r.get("known"))
    print(f"# {len(results)} pages in {time.monotonic() - started:.0f}s: "
          + ", ".join(f"{n} {s}" for s, n in counts.most_common()) + f". {usable} ready to add by url.")


def cmd_links(args, p: gf.Paths) -> None:
    """List product links on shop search pages, category pages or gift guides."""
    known = {gf.normalise_url(c["url"]) for c in gf.load_cards(p) if c.get("url")}
    fetcher = scout.Fetcher(delay=args.delay)
    for page in scout.links_many(_read_urls(args), any_host=args.any_shop, limit=args.limit, fetcher=fetcher):
        page["links"] = [l for l in page["links"] if gf.normalise_url(l["url"]) not in known]
        print(json.dumps(page, ensure_ascii=False))


def cmd_pool(args, p: gf.Paths) -> None:
    """Checked products from earlier runs that are not a card yet."""
    settings = gf.load_settings(p)
    for e in gf.pool_candidates(p, settings):
        print(json.dumps({k: e[k] for k in ("url", "title", "shop", "price", "tier", "in_stock", "age_days", "fresh")},
                         ensure_ascii=False))


def cmd_deck(args, p: gf.Paths) -> None:
    settings = gf.load_settings(p)
    for c in gf.build_deck(settings, gf.load_cards(p)):
        if c["type"] == "question":
            print(f"?  {c['question']}")
        else:
            print(f"{c['tier']:<6} €{c['price']:>7.2f}  {c['kind']:<8} {c['title']} ({c['shop']})")


def cmd_stats(args, p: gf.Paths) -> None:
    state = gf.read_json(p.state, {})
    since = None if args.all else state.get("last_learn_at")
    print(json.dumps({"since": since, **gf.taste_stats(gf.load_swipes(p, since))}, indent=2, ensure_ascii=False))


def cmd_mark_learned(args, p: gf.Paths) -> None:
    state = gf.read_json(p.state, {})
    state["last_learn_at"] = gf.now_iso()
    gf.write_json(p.state, state)
    print(f"learned up to {state['last_learn_at']}")


def cmd_wishlist(args, p: gf.Paths) -> None:
    path = gf.write_wishlist(p)
    print(path.read_text(encoding="utf-8"))


def cmd_remove(args, p: gf.Paths) -> None:
    for card_id in args.ids:
        card = gf.record_swipe(p, card_id, "removed")
        print(f"removed {card['title']}")


def cmd_check_links(args, p: gf.Paths) -> None:
    """Report wishlist (or all unseen) cards whose page no longer answers."""
    cards = gf.load_cards(p)
    wanted = ("liked", "loved") if not args.unseen else ("new",)
    for c in cards:
        if c.get("type") != "gift" or c["status"] not in wanted:
            continue
        status = check_url(c["url"])
        if status != "ok":
            print(f"{c['id']}: {status} {c['url']}")
    print("done")


def check_url(url: str) -> str:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0 (gift-feed link check)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return "ok" if r.status < 400 else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        # 403/429 usually means the shop blocks scripts, not that the product is gone.
        return "ok" if e.code in (401, 403, 429) else f"HTTP {e.code}"
    except Exception as e:  # network errors, timeouts
        return f"error {type(e).__name__}"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="cli.py", description=__doc__)
    ap.add_argument("--me", help="data folder (default: me/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create me/ from templates")

    s = sub.add_parser("settings", help="save price tiers and targets from /setup answers")
    s.add_argument("--min-price", type=float, required=True, help="the least a gift should cost")
    s.add_argument("--small-max", type=float, required=True, help="up to this price a gift is small")
    s.add_argument("--big-min", type=float, required=True, help="from this price a gift is big")
    s.add_argument("--big-max", type=float, required=True, help="the most anyone should spend on a big wish")
    s.add_argument("--big", type=int, required=True, help="how many big wishes")
    s.add_argument("--medium", type=int, required=True, help="how many medium gifts")
    s.add_argument("--small", type=int, required=True, help="how many small gifts")
    s.add_argument("--deck-size", type=int, default=15)

    a = sub.add_parser("add-card", help="add cards (JSON object or list) from stdin or --file")
    a.add_argument("--file")

    st = sub.add_parser("status", help="what the next /scout should find")
    st.add_argument("--urls", action="store_true", help="also list product URLs already found")
    st.add_argument("--next-deck", action="store_true", help="count for the deck after the one on screen now")

    ch = sub.add_parser("check", help="read product pages in parallel: title, price, stock, image, tier")
    ch.add_argument("urls", nargs="*")
    ch.add_argument("--file", help="a file with one URL per line")
    ch.add_argument("--workers", type=int, default=scout.WORKERS, help="shops read at the same time")
    ch.add_argument("--delay", type=float, default=scout.PER_HOST_DELAY, help="seconds between pages of one shop")

    li = sub.add_parser("links", help="list product links on shop search pages, categories or gift guides")
    li.add_argument("urls", nargs="*")
    li.add_argument("--file", help="a file with one URL per line")
    li.add_argument("--any-shop", action="store_true", help="for gift guides: keep links to other sites")
    li.add_argument("--limit", type=int, default=30, help="links per page")
    li.add_argument("--delay", type=float, default=scout.PER_HOST_DELAY)

    sub.add_parser("pool", help="checked products from earlier runs that are not a card yet")

    sub.add_parser("deck", help="preview today's deck")

    sa = sub.add_parser("stats", help="taste stats from swipes since the last /learn")
    sa.add_argument("--all", action="store_true", help="use every swipe ever")

    sub.add_parser("mark-learned", help="remember that /learn processed all swipes so far")
    sub.add_parser("wishlist", help="regenerate and print me/wishlist.md")

    r = sub.add_parser("remove", help="remove cards from the wishlist")
    r.add_argument("ids", nargs="+")

    c = sub.add_parser("check-links", help="find wishlist links that stopped working")
    c.add_argument("--unseen", action="store_true", help="check unseen feed cards instead")

    args = ap.parse_args(argv)
    p = gf.paths(args.me)
    handler = globals()["cmd_" + args.cmd.replace("-", "_")]
    try:
        handler(args, p)
    except ValueError as e:
        raise SystemExit(f"error: {e}")


if __name__ == "__main__":
    main()
