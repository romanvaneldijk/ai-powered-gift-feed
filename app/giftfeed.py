"""Core logic for the gift feed: cards, price tiers, deck building, swipes and stats.

Standard library only, so the repo runs with plain Python 3.10+.
All personal data lives in the git-ignored `me/` folder.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIERS = ("big", "medium", "small")

# How much each action counts when learning taste.
ACTION_WEIGHTS = {"love": 2.0, "like": 1.0, "later": 0.3, "dismiss": -1.0, "removed": -0.5}
# "Kinda, maybe later" brings a gift back on another day, at most this many times.
MAX_LATER = 2
CARD_KINDS = ("fit", "explore", "wildcard")


@dataclass
class Paths:
    me: Path

    @property
    def settings(self) -> Path:
        return self.me / "settings.json"

    @property
    def profile(self) -> Path:
        return self.me / "profile.md"

    @property
    def sources(self) -> Path:
        return self.me / "sources.md"

    @property
    def feed(self) -> Path:
        return self.me / "feed"

    @property
    def swipes(self) -> Path:
        return self.me / "swipes.jsonl"

    @property
    def wishlist(self) -> Path:
        return self.me / "wishlist.md"

    @property
    def state(self) -> Path:
        return self.me / "state.json"

    @property
    def pool(self) -> Path:
        return self.me / "pool.json"


def paths(me: str | Path | None = None) -> Paths:
    me = Path(me or os.environ.get("GIFTFEED_ME") or ROOT / "me")
    return Paths(me=me)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- settings


def load_settings(p: Paths) -> dict:
    settings = read_json(p.settings, None)
    if settings is None:
        raise SystemExit("No me/settings.json yet. Run /setup first.")
    validate_settings(settings)
    return settings


def validate_settings(s: dict) -> None:
    tiers = s.get("tiers") or {}
    for t in TIERS:
        lo, hi = tiers.get(t, [None, None])
        if lo is None or hi is None or lo < 0 or hi <= lo:
            raise ValueError(f"settings.tiers.{t} must be [min, max] with max > min, got {tiers.get(t)}")
    if not tiers["small"][1] <= tiers["medium"][0] + 1e-9 or not tiers["medium"][1] <= tiers["big"][0] + 1e-9:
        raise ValueError("price tiers must not overlap: small < medium < big")
    targets = s.get("targets") or {}
    for t in TIERS:
        if int(targets.get(t, -1)) < 0:
            raise ValueError(f"settings.targets.{t} must be 0 or more")
    if int(s.get("deck_size", 0)) < 1:
        raise ValueError("settings.deck_size must be at least 1")


def build_settings(min_price: float, small_max: float, big_min: float, big_max: float,
                   big: int, medium: int, small: int, deck_size: int = 15) -> dict:
    """Turn the four /setup price answers and three counts into settings."""
    s = {
        "tiers": {
            "small": [min_price, small_max],
            "medium": [small_max, big_min],
            "big": [big_min, big_max],
        },
        "targets": {"big": big, "medium": medium, "small": small},
        "deck_size": deck_size,
        "explore_share": 0.2,
        "wildcard_share": 0.1,
        "currency": "EUR",
    }
    validate_settings(s)
    return s


def tier_for_price(price: float, settings: dict) -> str | None:
    """Return the tier a price falls in, or None when it is outside every range.

    Boundaries belong to the higher tier: with small 5-25 and medium 25-75, 25 is medium.
    """
    tiers = settings["tiers"]
    for t in TIERS:  # big first, so shared boundaries go to the higher tier
        lo, hi = tiers[t]
        if lo <= price <= hi:
            return t
    return None


# ---------------------------------------------------------------- cards

REQUIRED_CARD_FIELDS = ("title", "shop", "url", "price")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "card"


def normalise_url(url: str) -> str:
    url = url.strip()
    url = re.sub(r"[?#].*$", "", url)  # drop tracking parameters and fragments
    return url.rstrip("/").lower()


def load_cards(p: Paths) -> list[dict]:
    cards = []
    if p.feed.exists():
        for f in sorted(p.feed.glob("*.json")):
            card = read_json(f, None)
            if card:
                cards.append(card)
    return cards


def save_card(p: Paths, card: dict) -> None:
    write_json(p.feed / f"{card['id']}.json", card)


def add_card(p: Paths, raw: dict, settings: dict) -> dict:
    """Validate a card found by /scout and store it. Raises ValueError on bad input.

    A card may give only its url (plus tags, reason, kind and source) when `cli.py check` read
    that page recently: title, shop, price and image then come from the checked page.
    """
    if raw.get("type") == "question":
        return add_question(p, raw)
    raw = fill_from_pool(p, raw)
    missing = [f for f in REQUIRED_CARD_FIELDS if raw.get(f) in (None, "")]
    if missing:
        raise ValueError(f"card is missing {', '.join(missing)}")
    if not re.match(r"^https?://", str(raw["url"])):
        raise ValueError("card url must start with http:// or https://")
    try:
        price = round(float(raw["price"]), 2)
    except (TypeError, ValueError):
        raise ValueError(f"price must be a number in euros, got {raw['price']!r}")
    tier = tier_for_price(price, settings)
    if tier is None:
        raise ValueError(f"price €{price} is outside all of the user's price tiers")
    key = normalise_url(raw["url"])
    for existing in load_cards(p):
        if existing.get("url") and normalise_url(existing["url"]) == key:
            raise ValueError(f"duplicate: this product is already card {existing['id']}")
    kind = raw.get("kind", "fit")
    if kind not in CARD_KINDS:
        raise ValueError(f"kind must be one of {', '.join(CARD_KINDS)}")
    card = {
        "id": f"{slug(raw['title'])}-{uuid.uuid4().hex[:6]}",
        "type": "gift",
        "title": str(raw["title"]).strip(),
        "shop": str(raw["shop"]).strip(),
        "url": str(raw["url"]).strip(),
        "price": price,
        "tier": tier,
        "image": raw.get("image") or None,
        "tags": sorted({slug(t) for t in raw.get("tags", []) if str(t).strip()}),
        "reason": (raw.get("reason") or "").strip(),
        "kind": kind,
        "source": (raw.get("source") or "").strip(),
        "found_at": now_iso(),
        "status": "new",
    }
    save_card(p, card)
    return card


# ---------------------------------------------------------------- pool of checked products

# Facts from a checked page are trusted this long; older ones must be checked again.
POOL_FRESH_DAYS = 3
# Unused checked products are kept this long, so the next /scout can start from them.
POOL_KEEP_DAYS = 14


def load_pool(p: Paths) -> dict[str, dict]:
    return read_json(p.pool, {})


def _age_days(iso: str) -> float:
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 1e9
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


def save_checked(p: Paths, results: list[dict], settings: dict) -> list[dict]:
    """Store checked product pages in the pool and annotate each result with its tier,
    whether it is already a card, and whether it can be used as it is."""
    pool = load_pool(p)
    known = {normalise_url(c["url"]) for c in load_cards(p) if c.get("url")}
    for r in results:
        key = normalise_url(r.get("final_url") or r["url"])
        r["known"] = key in known or normalise_url(r["url"]) in known
        if r.get("status") != "ok":
            pool.pop(key, None)
            continue
        currency = r.get("currency") or "EUR"
        if currency != "EUR":
            r["status"], r["note"] = "not-eur", f"price is in {currency}"
            pool.pop(key, None)
            continue
        r["tier"] = tier_for_price(r["price"], settings)
        if r["tier"] is None:
            r["note"] = "price is outside your tiers"
        if r.get("in_stock") is None:
            r["note"] = "stock unknown: confirm on the page before using it"
        pool[key] = {
            "url": r.get("final_url") or r["url"],
            "title": r["title"],
            "shop": r.get("shop"),
            "price": r["price"],
            "image": r.get("image"),
            "in_stock": r.get("in_stock"),
            "checked_at": now_iso(),
        }
    pool = {k: v for k, v in pool.items() if _age_days(v["checked_at"]) <= POOL_KEEP_DAYS and k not in known}
    write_json(p.pool, pool)
    return results


def pool_candidates(p: Paths, settings: dict) -> list[dict]:
    """Checked products that are not a card yet, freshest first, with tier and age."""
    known = {normalise_url(c["url"]) for c in load_cards(p) if c.get("url")}
    out = []
    for key, e in load_pool(p).items():
        if key in known or _age_days(e["checked_at"]) > POOL_KEEP_DAYS:
            continue
        tier = tier_for_price(e["price"], settings)
        if tier is None:
            continue
        age = _age_days(e["checked_at"])
        out.append(e | {"tier": tier, "age_days": round(age, 1), "fresh": age <= POOL_FRESH_DAYS})
    return sorted(out, key=lambda e: e["age_days"])


def fill_from_pool(p: Paths, raw: dict) -> dict:
    """Fill a card's title, shop, price and image from a recently checked page."""
    if not raw.get("url"):
        return raw
    entry = load_pool(p).get(normalise_url(str(raw["url"])))
    needs = [f for f in ("title", "shop", "price", "image") if raw.get(f) in (None, "")]
    if not entry or not needs:
        return raw
    if _age_days(entry["checked_at"]) > POOL_FRESH_DAYS:
        raise ValueError("this page was checked too long ago: run `cli.py check` on it again")
    if entry.get("in_stock") is False:
        raise ValueError("the checked page says this product is out of stock")
    if entry.get("in_stock") is None and raw.get("in_stock") is not True:
        raise ValueError('stock is unknown: confirm it on the page, then add "in_stock": true')
    filled = dict(raw)
    for f in needs:
        filled[f] = entry.get(f)
    filled["url"] = entry["url"]
    return filled


def add_question(p: Paths, raw: dict) -> dict:
    options = [str(o).strip() for o in raw.get("options", []) if str(o).strip()]
    if not raw.get("question") or len(options) < 2:
        raise ValueError("a question card needs 'question' and at least two 'options'")
    card = {
        "id": f"q-{slug(raw['question'])}-{uuid.uuid4().hex[:6]}",
        "type": "question",
        "question": str(raw["question"]).strip(),
        "options": options[:4],
        "found_at": now_iso(),
        "status": "new",
    }
    save_card(p, card)
    return card


# ---------------------------------------------------------------- deck


def wishlist_counts(cards: list[dict]) -> Counter:
    return Counter(c["tier"] for c in cards if c.get("type") == "gift" and c["status"] in ("liked", "loved"))


def tier_quota(settings: dict, cards: list[dict], deck_size: int | None = None) -> dict[str, int]:
    """How many cards of each tier today's deck should hold.

    Follows what the wishlist still needs; once every tier is full it follows the targets.
    """
    deck_size = deck_size or int(settings["deck_size"])
    targets = {t: int(settings["targets"][t]) for t in TIERS}
    have = wishlist_counts(cards)
    need = {t: max(targets[t] - have[t], 0) for t in TIERS}
    weights = need if sum(need.values()) > 0 else targets
    if sum(weights.values()) == 0:
        weights = {t: 1 for t in TIERS}
    total = sum(weights.values())
    raw = {t: deck_size * weights[t] / total for t in TIERS}
    quota = {t: int(raw[t]) for t in TIERS}
    # Largest remainder, so the quotas add up to the deck size.
    for t in sorted(TIERS, key=lambda t: raw[t] - quota[t], reverse=True):
        if sum(quota.values()) >= deck_size:
            break
        if weights[t] > 0:
            quota[t] += 1
    return quota


def build_deck(settings: dict, cards: list[dict], deck_size: int | None = None) -> list[dict]:
    """Pick today's deck from unseen cards: per-tier quotas, a mix of fit/explore/wildcard,
    at most one question card, cheapest-first never — the order is shuffled by tier."""
    deck_size = deck_size or int(settings["deck_size"])
    unseen = [c for c in cards if c["status"] == "new" or comes_back_today(c)]
    gifts = [c for c in unseen if c.get("type") == "gift"]
    questions = [c for c in unseen if c.get("type") == "question"]
    quota = tier_quota(settings, cards, deck_size)

    by_tier = {t: sorted((c for c in gifts if c["tier"] == t), key=lambda c: c["found_at"]) for t in TIERS}
    explore_n = round(deck_size * float(settings.get("explore_share", 0.2)))
    wildcard_n = round(deck_size * float(settings.get("wildcard_share", 0.1)))

    def take(pool: list[dict], n: int) -> list[dict]:
        # Honour the explore and wildcard shares inside each tier where possible.
        picked: list[dict] = []
        for kind, share in (("wildcard", wildcard_n), ("explore", explore_n)):
            want = round(n * share / deck_size) if deck_size else 0
            for c in [c for c in pool if c["kind"] == kind][:want]:
                picked.append(c)
        for c in pool:
            if len(picked) >= n:
                break
            if c not in picked:
                picked.append(c)
        return picked[:n]

    deck: list[dict] = []
    for t in TIERS:
        deck += take(by_tier[t], quota[t])
    # Not enough cards in some tier: fill from the other tiers rather than show a short deck,
    # cheapest tier first so a shortfall never turns into a deck of expensive gifts.
    if len(deck) < deck_size:
        rest = [c for t in reversed(TIERS) for c in by_tier[t] if c not in deck]
        deck += rest[: deck_size - len(deck)]
    # Interleave tiers so the deck is not all big gifts first.
    deck = interleave(deck)
    if questions:
        deck.insert(min(len(deck), deck_size // 2), questions[0])
    return deck


def comes_back_today(card: dict, today: date | None = None) -> bool:
    """A "maybe later" gift returns from the next day on, until it was postponed MAX_LATER times."""
    if card.get("status") != "later" or int(card.get("later_count", 0)) > MAX_LATER:
        return False
    swiped = (card.get("swiped_at") or "")[:10]
    return bool(swiped) and swiped < (today or date.today()).isoformat()


def interleave(cards: list[dict]) -> list[dict]:
    groups = {t: [c for c in cards if c["tier"] == t] for t in ("small", "medium", "big")}
    out: list[dict] = []
    while any(groups.values()):
        for t in ("small", "medium", "small", "big"):
            if groups[t]:
                out.append(groups[t].pop(0))
    return out


# ---------------------------------------------------------------- swipes

SWIPE_STATUS = {"like": "liked", "love": "loved", "later": "later", "dismiss": "dismissed", "removed": "removed"}


def record_swipe(p: Paths, card_id: str, action: str, answer: str | None = None) -> dict:
    card_path = p.feed / f"{card_id}.json"
    card = read_json(card_path, None)
    if card is None:
        raise ValueError(f"no card {card_id}")
    if card.get("type") == "question":
        if action != "answer" or not answer:
            raise ValueError("a question card needs action 'answer' with an answer")
        card["status"] = "answered"
        card["answer"] = answer
        entry = {"at": now_iso(), "card": card_id, "action": "answer", "question": card["question"], "answer": answer}
    else:
        if action not in SWIPE_STATUS:
            raise ValueError(f"unknown action {action}")
        card["status"] = SWIPE_STATUS[action]
        card["swiped_at"] = now_iso()
        if action == "later":
            card["later_count"] = int(card.get("later_count", 0)) + 1
        entry = {
            "at": now_iso(), "card": card_id, "action": action,
            "tier": card["tier"], "price": card["price"], "tags": card["tags"], "kind": card["kind"],
        }
    save_card(p, card)
    p.swipes.parent.mkdir(parents=True, exist_ok=True)
    with p.swipes.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if card.get("type") == "gift":
        write_wishlist(p)
    return card


def load_swipes(p: Paths, since: str | None = None) -> list[dict]:
    if not p.swipes.exists():
        return []
    out = []
    for line in p.swipes.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            if since is None or entry["at"] > since:
                out.append(entry)
    return out


def taste_stats(swipes: list[dict]) -> dict:
    """Score tags by swipes and summarise price comfort per tier, for /learn and the taste card."""
    tag_score: Counter = Counter()
    tag_seen: Counter = Counter()
    tier_actions: dict[str, Counter] = {t: Counter() for t in TIERS}
    tier_prices: dict[str, dict[str, list[float]]] = {t: {"kept": [], "dismissed": []} for t in TIERS}
    answers = []
    for s in swipes:
        if s["action"] == "answer":
            answers.append({"question": s["question"], "answer": s["answer"]})
            continue
        w = ACTION_WEIGHTS.get(s["action"], 0)
        for tag in s.get("tags", []):
            tag_score[tag] += w
            tag_seen[tag] += 1
        t = s.get("tier")
        if t in tier_actions:
            tier_actions[t][s["action"]] += 1
            if s["action"] in ("like", "love", "dismiss"):
                bucket = "kept" if s["action"] != "dismiss" else "dismissed"
                tier_prices[t][bucket].append(s["price"])
    likes = [(t, round(v, 1)) for t, v in tag_score.most_common() if v > 0]
    dislikes = [(t, round(v, 1)) for t, v in sorted(tag_score.items(), key=lambda kv: kv[1]) if v < 0]
    positive_total = sum(v for _, v in likes) or 1
    taste_card = [(t, round(100 * v / positive_total)) for t, v in likes[:4]]
    return {
        "swipes": len([s for s in swipes if s["action"] != "answer"]),
        "likes": likes,
        "dislikes": dislikes,
        "taste_card": taste_card,
        "tiers": {
            t: {
                "actions": dict(tier_actions[t]),
                "avg_kept_price": _avg(tier_prices[t]["kept"]),
                "avg_dismissed_price": _avg(tier_prices[t]["dismissed"]),
            }
            for t in TIERS
        },
        "answers": answers,
    }


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


# ---------------------------------------------------------------- wishlist

TIER_TITLES = {"big": "Big wishes", "medium": "Medium gifts", "small": "Small gifts"}


def wishlist_items(cards: list[dict]) -> dict[str, list[dict]]:
    items = {t: [] for t in TIERS}
    for c in cards:
        if c.get("type") == "gift" and c["status"] in ("liked", "loved"):
            items[c["tier"]].append(c)
    for t in TIERS:
        items[t].sort(key=lambda c: (c["status"] != "loved", c["price"]))
    return items


def write_wishlist(p: Paths) -> Path:
    settings = read_json(p.settings, None)
    items = wishlist_items(load_cards(p))
    lines = ["# My wishlist", "", f"_Updated {date.today().isoformat()}. Generated by the gift feed; edit cards with /wishlist._", ""]
    for t in TIERS:
        target = settings["targets"][t] if settings else "?"
        lines.append(f"## {TIER_TITLES[t]} ({len(items[t])}/{target})")
        lines.append("")
        if not items[t]:
            lines.append("_Nothing yet._")
        for c in items[t]:
            star = " ★" if c["status"] == "loved" else ""
            lines.append(f"- [{c['title']}]({c['url']}) · {c['shop']} · €{c['price']:.2f}{star} `{c['id']}`")
        lines.append("")
    p.wishlist.parent.mkdir(parents=True, exist_ok=True)
    p.wishlist.write_text("\n".join(lines), encoding="utf-8")
    return p.wishlist


# ---------------------------------------------------------------- streak


def update_streak(p: Paths, today: date | None = None) -> dict:
    today = today or date.today()
    state = read_json(p.state, {})
    last = state.get("last_feed_date")
    if last == today.isoformat():
        return state
    if last and (today - date.fromisoformat(last)).days == 1:
        state["streak"] = int(state.get("streak", 0)) + 1
    else:
        state["streak"] = 1
    state["last_feed_date"] = today.isoformat()
    write_json(p.state, state)
    return state
