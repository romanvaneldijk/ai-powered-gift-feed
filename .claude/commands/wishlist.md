---
description: View, tidy and export your wishlist
argument-hint: "[import | remove <card id> | check | export]"
---

Arguments: $ARGUMENTS

- **No arguments**: run `python3 app/cli.py wishlist` and show the list grouped by tier (big, medium, small), with how many gifts each tier holds. There is no target per tier: the list can be as long as the user likes. Then offer the options below in one line.
- **`import`** (optionally followed by the list itself): bring in a wishlist the user already has.
  1. If no list came with the command, ask the user to paste it: one gift per line, as a link or in
     words, for example `https://www.bol.com/nl/nl/p/...` or `a good pepper grinder`. Bullets,
     numbers and empty lines are fine. If they point to an online list (a lootjestrekken.nl,
     bol.com or Amazon list), ask them to copy its items or links into the chat; do not log in or
     work around a page that blocks scripts.
  2. Save the pasted text to a temp file and run `python3 app/cli.py import-list --file <tmpfile>`.
     It reads every link's page in parallel and prints one JSON line per gift with a `todo`:
     - `add by url`: the page was read. Ready.
     - `read the page yourself`: `check` could not read it. Read those pages with WebFetch, all in
       one message, asking only for the title, current price in euros, stock and the `og:image` URL.
       If the shop does not sell in euros or the facts cannot be confirmed, tell the user and skip it.
     - `find it in a shop`: words only. Find the real product, in one round of parallel searches:
       a `WebSearch` per item (Dutch shops first, the user's `sources.md` shops and their `search:`
       URLs with `python3 app/cli.py links` where they fit), then one `python3 app/cli.py check` of
       the best 1 or 2 product pages per item. Pick the product that matches the words most closely
       (brand, model, colour, size when given) at a fair price in euros. If the words are too vague
       to pick one product ("something for the garden", "a nice book"), do not guess: add it to the
       profile's **Search hints** as a `wish:` lead, so /scout finds ideas for it, and tell the user.
     - `already on the wishlist`: skip it.
  3. Add everything in one call with `python3 app/cli.py add-card --wishlist --file <tmpfile>` (a
     JSON list), each with the `url`, 2 to 4 `tags` (reusing profile words, so /learn counts them),
     a short `reason` such as "From your own list", `"source": "imported"` and `"imported_from"`
     set to the user's original line. For pages you read with WebFetch, also give `title`, `shop`,
     `price` and `image`. These gifts go straight on the wishlist and count as "put it on the list"
     for /learn. A price outside the user's tiers is put with the nearest tier; mention those. A
     product that was already a card in the feed is moved onto the wishlist.
  4. Finish with a short summary: how many gifts were imported per tier, which words matched which
     product (title and price, so the user can say "no, the other one"), and what was skipped and why.
     Then offer to run `/learn` so the imported gifts shape the next decks.
- **`remove <id> ...`**: run `python3 app/cli.py remove <id> ...`. This also teaches /learn that the item was not quite right.
- **`check`**: run `python3 app/cli.py check-links`. For every item it reports, open the product page yourself to see whether the product is gone, sold out or changed price. Tell the user what you found and offer to remove dead items.
- **`export`**: create `me/export/wishlist.html`, a simple, good-looking single page that the user can send to family: a section per tier (big wishes, medium gifts, small gifts), each item with its title, shop, price and a link to the shop. Use the shop's product image if the card has one. No personal profile data on this page. Then give the path, and mention they can also paste the list into lootjestrekken.nl.
