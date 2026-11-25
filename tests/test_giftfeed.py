import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import giftfeed as gf  # noqa: E402

SETTINGS = gf.build_settings(5, 25, 80, 200, big=1, medium=3, small=6, deck_size=10)


def card(i, price, kind="fit", tags=("cooking",)):
    return {"title": f"Gift {i}", "shop": "Shop", "url": f"https://shop.example/p/{i}",
            "price": price, "tags": list(tags), "kind": kind, "reason": "r"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = gf.paths(self.tmp.name)
        gf.write_json(self.p.settings, SETTINGS)

    def tearDown(self):
        self.tmp.cleanup()


class TierTests(unittest.TestCase):
    def test_tier_for_price(self):
        self.assertEqual(gf.tier_for_price(10, SETTINGS), "small")
        self.assertEqual(gf.tier_for_price(25, SETTINGS), "medium")  # boundary goes up
        self.assertEqual(gf.tier_for_price(80, SETTINGS), "big")
        self.assertIsNone(gf.tier_for_price(2, SETTINGS))
        self.assertIsNone(gf.tier_for_price(500, SETTINGS))

    def test_overlapping_tiers_refused(self):
        with self.assertRaises(ValueError):
            gf.build_settings(5, 50, 40, 200, 1, 1, 1)


class CardTests(Base):
    def test_add_card_assigns_tier_and_refuses_duplicates(self):
        c = gf.add_card(self.p, card(1, 30), SETTINGS)
        self.assertEqual(c["tier"], "medium")
        with self.assertRaises(ValueError):
            gf.add_card(self.p, card(1, 30) | {"url": "https://shop.example/p/1?utm=x"}, SETTINGS)

    def test_out_of_range_and_missing_fields(self):
        with self.assertRaises(ValueError):
            gf.add_card(self.p, card(2, 999), SETTINGS)
        with self.assertRaises(ValueError):
            gf.add_card(self.p, {"title": "x"}, SETTINGS)


class DeckTests(Base):
    def fill(self, n_per_tier=10):
        prices = {"small": 10, "medium": 40, "big": 120}
        i = 0
        for t, price in prices.items():
            for k in range(n_per_tier):
                kind = "explore" if k % 5 == 0 else "fit"
                gf.add_card(self.p, card(i, price, kind), SETTINGS)
                i += 1

    def test_quota_follows_targets_and_sums_to_deck_size(self):
        q = gf.tier_quota(SETTINGS, [])
        self.assertEqual(sum(q.values()), 10)
        self.assertEqual(q, {"big": 1, "medium": 3, "small": 6})

    def test_full_tier_gets_no_cards(self):
        self.fill()
        cards = gf.load_cards(self.p)
        big = next(c for c in cards if c["tier"] == "big")
        gf.record_swipe(self.p, big["id"], "like")
        q = gf.tier_quota(SETTINGS, gf.load_cards(self.p))
        self.assertEqual(q["big"], 0)
        self.assertEqual(sum(q.values()), 10)

    def test_deck_mixes_tiers_and_is_not_all_expensive(self):
        self.fill()
        deck = gf.build_deck(SETTINGS, gf.load_cards(self.p))
        tiers = [c["tier"] for c in deck]
        self.assertEqual(len(deck), 10)
        self.assertEqual(tiers.count("big"), 1)
        self.assertEqual(tiers.count("small"), 6)
        self.assertNotEqual(tiers[0], "big")

    def test_short_tier_is_filled_from_others(self):
        for i in range(12):
            gf.add_card(self.p, card(i, 10), SETTINGS)
        deck = gf.build_deck(SETTINGS, gf.load_cards(self.p))
        self.assertEqual(len(deck), 10)

    def test_question_card_is_added_once(self):
        self.fill()
        gf.add_card(self.p, {"type": "question", "question": "Tea or coffee?", "options": ["Tea", "Coffee"]}, SETTINGS)
        deck = gf.build_deck(SETTINGS, gf.load_cards(self.p))
        self.assertEqual(sum(1 for c in deck if c["type"] == "question"), 1)


class SwipeTests(Base):
    def test_swipes_update_wishlist_and_stats(self):
        a = gf.add_card(self.p, card(1, 10, tags=["coffee"]), SETTINGS)
        b = gf.add_card(self.p, card(2, 40, tags=["gaming"]), SETTINGS)
        c = gf.add_card(self.p, card(3, 12, tags=["coffee", "kitchen"]), SETTINGS)
        gf.record_swipe(self.p, a["id"], "love")
        gf.record_swipe(self.p, b["id"], "dismiss")
        gf.record_swipe(self.p, c["id"], "like")
        wl = self.p.wishlist.read_text()
        self.assertIn("Gift 1", wl)
        self.assertNotIn("Gift 2", wl)
        self.assertIn("Small gifts (2/6)", wl)
        stats = gf.taste_stats(gf.load_swipes(self.p))
        self.assertEqual(stats["likes"][0], ("coffee", 3.0))
        self.assertIn(("gaming", -1.0), stats["dislikes"])
        gf.record_swipe(self.p, a["id"], "removed")
        self.assertNotIn("Gift 1", self.p.wishlist.read_text())

    def test_maybe_later_comes_back_another_day_at_most_twice(self):
        c = gf.add_card(self.p, card(1, 10), SETTINGS)
        gf.record_swipe(self.p, c["id"], "later")
        saved = json.loads((self.p.feed / f"{c['id']}.json").read_text())
        self.assertEqual(saved["status"], "later")
        self.assertFalse(gf.comes_back_today(saved))  # not on the same day
        tomorrow = date.fromisoformat(saved["swiped_at"][:10]).toordinal() + 1
        self.assertTrue(gf.comes_back_today(saved, date.fromordinal(tomorrow)))
        saved["later_count"] = gf.MAX_LATER + 1
        self.assertFalse(gf.comes_back_today(saved, date.fromordinal(tomorrow)))
        self.assertNotIn("Gift 1", self.p.wishlist.read_text())

    def test_already_own_it(self):
        c = gf.add_card(self.p, card(1, 10, tags=["board-games"]), SETTINGS)
        gf.record_swipe(self.p, c["id"], "owned")
        cards = gf.load_cards(self.p)
        self.assertEqual(cards[0]["status"], "owned")
        self.assertEqual(gf.build_deck(SETTINGS, cards), [])  # never shown again
        self.assertNotIn("Gift 1", self.p.wishlist.read_text())  # not on the wishlist
        stats = gf.taste_stats(gf.load_swipes(self.p))
        self.assertEqual(stats["likes"], [("board-games", 0.7)])
        self.assertEqual(stats["already_owned"], ["Gift 1"])

    def test_question_answer(self):
        q = gf.add_card(self.p, {"type": "question", "question": "Tea?", "options": ["Yes", "No"]}, SETTINGS)
        gf.record_swipe(self.p, q["id"], "answer", "Yes")
        stats = gf.taste_stats(gf.load_swipes(self.p))
        self.assertEqual(stats["answers"], [{"question": "Tea?", "answer": "Yes"}])

    def test_streak(self):
        self.assertEqual(gf.update_streak(self.p, date(2026, 12, 1))["streak"], 1)
        self.assertEqual(gf.update_streak(self.p, date(2026, 12, 2))["streak"], 2)
        self.assertEqual(gf.update_streak(self.p, date(2026, 12, 2))["streak"], 2)
        self.assertEqual(gf.update_streak(self.p, date(2026, 12, 5))["streak"], 1)


class ExampleDataTests(unittest.TestCase):
    def test_examples_are_valid(self):
        ex = gf.ROOT / "examples"
        settings = json.loads((ex / "settings.json").read_text())
        gf.validate_settings(settings)
        for f in (ex / "feed").glob("*.json"):
            c = json.loads(f.read_text())
            if c["type"] == "gift":
                self.assertEqual(gf.tier_for_price(c["price"], settings), c["tier"], f.name)


if __name__ == "__main__":
    unittest.main()
