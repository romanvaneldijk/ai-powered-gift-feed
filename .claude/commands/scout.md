---
description: Find new gift ideas for your feed, or add a favourite site, Instagram gift guide or LTK page
argument-hint: "[add-site <url> | add-insta <url> | next]"
---

Arguments: $ARGUMENTS

## If the arguments start with `add-site` or `add-insta`

Add the URL to the right list in `me/sources.md` ("Favourite websites", "Instagram gift guides", or "LTK and other creator shops" for shopltk.com, liketoknow.it and similar creator shop pages; add that heading if it is missing), unless it is already there. Confirm in one line and stop. Do not scout.

## Otherwise: find gift ideas, fast

Goal: add enough good, real, buyable gift cards for the next deck, quickly. Quality beats quantity. Never invent a product, URL, price or image URL: every card must come from a page that was actually read: by you in this session, or by `cli.py check` in the last 3 days (`add-card` enforces this).

**Speed rules.** The slow part of scouting is reading pages one by one. So:
- Let `python3 app/cli.py check` read product pages. It reads many pages in parallel (politely: one page at a time per shop) and returns title, price, stock, image and tier without using Claude. Read a page yourself with WebFetch only when `check` could not.
- Batch everything: put all web searches of a round in one message, and all URLs of a round in one `check` call.
- At most two rounds of searching. Stop as soon as `cards_to_find` is covered plus 2 spare.
- No subagents: they add start-up time and `check` already keeps page reading cheap.

1. **Status.** Run `python3 app/cli.py status --urls` (with `--next-deck` if the arguments say `next`, which `/feed` uses while the user is swiping). It gives the price tiers, `cards_to_find` per tier, `checked_products_waiting` and `known_urls`. If every tier needs 0 cards, say the deck is already full and stop.
2. **Profile.** Read `me/profile.md` and `me/sources.md`. Start from the profile's "Search hints" section if it has one: use those words for your search queries before inventing your own. "Everyday upgrades", "Secret wishes", "Fandoms and favourites", "Taste and style" and "Life right now" (when present) tell you what to look for; interests, likes, dislikes and "Curious about" still count. Skip anything from "Already have plenty of" unless it is a clear upgrade of what they own, and then say so in the card's reason. Never suggest anything listed under "Already own" or a near copy of it (the same product from another shop, or a different colour of it); products in the same category are fine, for example a different board game.
3. **Plan the mix.** Of the cards you add, roughly 70% should closely fit the profile (`"kind": "fit"`), 20% should explore an interest from "Curious about" or one the user has not seen yet (`"kind": "explore"`), and 10% can be a surprising wildcard (`"kind": "wildcard"`). Respect the hard no-gos always.
4. **Leftovers first.** If `checked_products_waiting` is not empty, run `python3 app/cli.py pool`. These are products checked on an earlier run that were not used. Take the ones that fit; those marked `"fresh": false` go into this run's `check` call again.
5. **Find candidate product pages in one round**, in a single message with parallel tool calls:
   - **Favourite websites: shop search first.** Turn the "Search hints" and interests into 3 to 6 short Dutch or English searches (`"koffie cadeau" "linnen schort" "wollen sjaal"`) and run them all in one call: `python3 app/cli.py search "<words>" "<words>" …`. It searches every favourite shop that has a recipe with the shop's own search page (built-in recipes, or a `search:` URL in `sources.md`), in parallel and politely, and prints one JSON line per shop and search with name, price, tier, photo and link of each product, already without known cards and prices outside the tiers. Pick the products that fit and put their URLs in step 6's `check` call (search results are leads, not cards). Add `--shop <host>` to search only some shops. Shop searches are fuzzy, so results whose name, type or tags do not hold a word of the search are left out (`dropped` says how many): search with product words (`trui`, `koffiemolen`, `sjaal`), not only themes like `cadeau`.
     For each shop it reports `no-recipe` or `blocked`, use a `WebSearch` per shop and theme instead, such as `site:bol.com koffie cadeau`. A shop that answers `no-results` for every search may have a broken recipe: try `links` on its search page once, or a `WebSearch`.
   - **Open web search:** 2 to 4 `WebSearch` queries for gift guides and shops built from the "Search hints" and the profile's interests and price tiers, Dutch shops first.
   - **Instagram gift guides:** one `WebFetch` of the post or account in the same message. Instagram often blocks automated reads. If it does, do not wait for the user: carry on, and at the end ask them to paste the caption or a screenshot next time. When it works, find each product it names in a shop and use the shop's page and photo, never the creator's photo. Put the Instagram link in the card's `source` as credit.
   - **LTK and other creator shops** (one page per run, at most; LTK does not want AI crawling at scale): put the page in the same round's `python3 app/cli.py links --any-shop <ltk page>` call. Each link's text holds the product's name, often its brand, and a price in dollars; use those as leads only. Do not `check` the `on.ltk.com` redirect links: LTK's robots.txt asks scripts not to follow them, and never work around that. Instead, for the products that fit the profile, find the same product in a shop that sells in euros: with `search` when it is one of the favourite shops, otherwise with one batched `WebSearch` per product (brand + name, Dutch or EU shop first, such as `H&M heeled ankle boots black site:hm.com/nl_nl`). Put the shop pages you find in step 6's `check` call and make the card from the shop's page, price and photo. A close match from another shop is fine when the exact product is not sold in euros; say so in the reason. Use the LTK page as the card's `source` for credit, and never use the creator's photos.
   - Gift guide or category pages found by search: `python3 app/cli.py links --any-shop <guide urls>` lists the shop links in them.
   Collect about twice as many product URLs as `cards_to_find` needs. Skip URLs in `known_urls`.
6. **Check them all in one call:** `python3 app/cli.py check <url> <url> …` (or `--file urls.txt` for many). One line per page:
   - `ok` with a `tier` and `"in_stock": true`: ready. Use it.
   - `ok` with a note that the stock is unknown: use it only after you confirm stock on the page, then add `"in_stock": true` to the card.
   - `out-of-stock`, `gone`, or a price outside the tiers (no `tier`): drop it.
   - `blocked` or `no-product`: only if you still need cards, read those pages with WebFetch, all in one message, asking only for the title, current price in euros, stock, and the `og:image` URL. Drop the page if any of these cannot be confirmed.
   If you are still short, do one more round of steps 5 and 6 for the tiers that are short, then stop.
7. **Add the cards** with `python3 app/cli.py add-card --file <tmpfile>` (a JSON list). For pages `check` read, give only the URL and your own fields; title, shop, price and image come from the checked page:
   ```json
   {
     "url": "https://www.example-shop.nl/product-page",
     "tags": ["coffee", "kitchen", "practical"],
     "reason": "Because you liked the pour-over kit",
     "kind": "fit",
     "source": "https://www.instagram.com/p/... (or the LTK page, guide or site where you found it)"
   }
   ```
   For pages you read yourself with WebFetch, also give `title`, `shop`, `price` (a number in euros) and `image`.
   Tags: 2 to 4 lowercase words, reusing the words from the profile where they fit, so /learn can count them. The reason is one short line the user sees on the card.
   The script assigns the price tier and refuses duplicates, stale checks and out-of-range prices; fix or drop whatever it skips.
8. **Bonus question.** Add one bonus question card per run if there is no unanswered one yet (`unanswered_questions` in the status). Make it a quick, fun question whose answer helps the next scout, for example `{"type": "question", "question": "Tea or coffee?", "options": ["Tea", "Coffee", "Both", "Neither"]}`.
9. **Remember shop search pages.** If a shop has no recipe yet but its search page worked with `links` this run, add it after the site in `sources.md`, for example `- https://www.example-shop.nl/ (search: https://www.example-shop.nl/zoeken?q={q})`. For a Shopify shop (product links contain `/products/`), use `https://<shop>/search/suggest.json?q={q}&resources[type]=product` instead. Next time `search` covers that shop and it needs no web search at all.
10. Finish with one line per tier saying how many cards were added, and suggest `/feed` (unless `/feed` is already running).
