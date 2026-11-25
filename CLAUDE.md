# AI-powered gift feed

A daily swipe game that builds a personal birthday or Christmas wishlist. The user plays it through
slash commands in Claude Code: `/setup`, `/scout`, `/feed`, `/learn` and `/wishlist`
(see `.claude/commands/`).

## Rules for Claude in this repo

- Speak English with the user, keep messages short, and make the quiz and feed feel like a game.
- All personal data lives in `me/`, which is git-ignored. Never commit, push or print `me/` contents
  to anywhere outside this machine, and never move personal data out of `me/`.
- Never invent a product, URL, price, stock status or image URL. Every card must come from a product
  page you actually read in this session, or that `cli.py check` read in the last 3 days.
- Prices are in euros. The price tiers and the number of gifts per tier come only from the user's own
  answers in `/setup` (stored in `me/settings.json`). Do not change them without the user's consent.
- Always add cards through `python3 app/cli.py add-card`, which validates them, assigns the tier and
  refuses duplicates. Do not write files in `me/feed/` by hand.
- Respect the user's hard no-gos in `me/profile.md` without exception.
- Be light on usage: read product pages with `python3 app/cli.py check` (parallel, no Claude tokens)
  and use WebFetch only for pages it cannot read; read each page once, batch searches, and stop
  scouting once the deck is full.
- Respect shop and Instagram terms: read single pages at a human pace for personal use, link to the
  source, and never republish a creator's photos.

## Code

- `app/giftfeed.py`: core logic (tiers, cards, deck, swipes, stats, wishlist). Standard library only.
- `app/cli.py`: the helper commands the slash commands call. Run `python3 app/cli.py -h`.
- `app/scout.py`: fast, polite page reader for /scout (JSON-LD, OpenGraph, microdata). Standard library only.
- `app/feed.py` and `app/static/index.html`: the local gift notebook page.
- Tests: `python3 -m unittest discover tests`.
- Keep the repo dependency-free (Python 3.10+ standard library only).
