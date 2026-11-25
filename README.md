# 🎁 AI-powered gift feed

**A daily gift notebook that builds your personal birthday or Christmas wishlist, powered by Claude Code.**

Every day Claude scouts fresh gift ideas from the web, your favourite shops and Instagram gift guides.
You flip through them in a notebook in your browser for a couple of minutes: add one to your wishlist,
or cross it out. Every swipe teaches it your taste, so tomorrow's ideas fit better. It works like
[lootjestrekken.nl](https://www.lootjestrekken.nl/), but keeps getting more personal.

Everything runs on your own machine. Your profile and wishlist stay in plain files on your computer.

![Today's gift idea, sketched in the notebook, next to your wishlist](docs/notebook-idea.png)

The feed is a notebook on your laptop screen. The left page sketches today's gift ideas one at a time,
and the right page is your wishlist, filling up as you go. Hover over a sketch to see the real photo.

![The end of the day: your taste so far and your wishlist per price tier](docs/notebook-done.png)

## Quick start

**You need:** [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) with a Claude
plan, Python 3.10 or newer, and git. No other installs or accounts.

```bash
git clone https://github.com/romanvaneldijk/ai-powered-gift-feed.git
cd ai-powered-gift-feed
claude
```

Then, inside Claude Code:

1. `/setup` plays the 2-minute onboarding quiz: your hobbies, style, no-gos, and your own price ranges.
2. `/scout` finds your first deck of gift ideas.
3. `/feed` opens your gift notebook in the browser. When you finish, Claude updates your profile.

Repeat `/scout` and `/feed` each day. Want to try the notebook first? Run
`python3 app/feed.py --demo` for a demo deck. Nothing gets saved.

## Commands

| Command | What it does |
| --- | --- |
| `/setup` | The onboarding quiz. Saves your profile and your price tiers. |
| `/scout` | Finds new gift ideas that fit your profile, checks each link and price, and adds them to your feed. |
| `/scout add-site <url>` | Adds a shop or blog you like. `/scout` checks it first on every run. |
| `/scout add-insta <url>` | Adds an Instagram gift guide post or account. Claude finds the products it mentions in a shop. |
| `/feed` | Today's gift notebook in your browser (→ on my list, ← not for me, ↑ love it), then `/learn`. |
| `/learn` | Updates your likes, dislikes and "curious about" from your latest swipes. Runs after `/feed` automatically. |
| `/wishlist` | Shows your list by tier. Use `remove <id>` to drop an item, `check` to find dead links, and `export` to make a page you can share with family. |

## How it learns

- **Your own price tiers.** You choose what counts as a small, medium and big gift, and how many of
  each you want, for example 1 big wish, 3 medium and 6 small. Each deck follows those numbers and
  what your wishlist still needs, so it is never all expensive items.
- **Swipes become taste.** Each card has tags such as `coffee`, `hiking` or `handmade`. Likes push
  a tag up, dismissals push it down, and "love it" counts double. `/learn` writes the result into
  `me/profile.md` as plain text you can read and edit.
- **It keeps exploring.** About 20% of each deck explores topics you haven't seen yet, and 10% is a
  wildcard, so your feed doesn't shrink to one hobby.
- **Bonus questions.** One quick question a day ("Tea or coffee?") sharpens the next search.

## Your data

All personal files live in `me/`, which is in `.gitignore`:

```
me/
├── settings.json   your price tiers, gift counts and deck size
├── profile.md      your taste profile (written by /setup and /learn)
├── sources.md      your favourite sites and Instagram gift guides
├── feed/           one JSON file per gift idea
├── swipes.jsonl    every swipe
└── wishlist.md     your wishlist, grouped by tier
```

> **Keep it private.** If you fork this repo on GitHub, your fork is public. `me/` is git-ignored so
> it is never committed, but do not force-add it, and keep a private repo if you want to back it up.

## Usage and cost

There is no extra cost beyond your Claude plan. The web search and page reading in `/scout` are the
heavy part. Based on a rough estimate (not yet measured), a daily `/scout` for 15 cards uses about
10 to 25% of one 5-hour Pro usage window. To keep it low, `/scout` reads each page once, hands page
reading to a cheaper subagent, and stops as soon as the deck is full. Check your own numbers with
`/usage` in Claude Code.

## Good to know

- **Instagram** often blocks automated page reads. When that happens, `/scout` asks you to paste
  the caption or drop in a screenshot of the post instead.
- **Shops:** Claude reads single product pages at a human pace for your personal use and always
  links to the shop. Respect each site's terms.
- **Prices and stock** change over time. `/wishlist check` finds links that no longer work.

## For developers

- `app/giftfeed.py`: core logic (tiers, cards, deck, swipes, stats). Python standard library only.
- `app/cli.py`: helper commands the slash commands call (`python3 app/cli.py -h`).
- `app/feed.py` and `app/static/index.html`: the local gift notebook page.
- `.claude/commands/`: the slash commands. Edit these to change how Claude behaves.
- Tests: `python3 -m unittest discover tests`

Contributions are welcome. Please open an issue first for bigger changes.

## License

[MIT](LICENSE)
