---
description: Find new gift ideas for your feed, or add a favourite site or Instagram gift guide
argument-hint: "[add-site <url> | add-insta <url>]"
---

Arguments: $ARGUMENTS

## If the arguments start with `add-site` or `add-insta`

Add the URL to the right list in `me/sources.md` ("Favourite websites" or "Instagram gift guides"), unless it is already there. Confirm in one line and stop. Do not scout.

## Otherwise: find today's gift ideas

Goal: add enough good, real, buyable gift cards for the next deck. Quality beats quantity. Never invent a product, URL, price or image URL: every card must come from a page you actually read.

1. Run `python3 app/cli.py status --urls`. It tells you the user's price tiers, how many cards are still needed per tier (`cards_to_find`) and every product URL already found. Skip products already in `known_urls`. If every tier needs 0 cards, say the deck is already full and stop.
2. Read `me/profile.md` (interests, style, likes, dislikes, curious about, hard no-gos, already own) and `me/sources.md`.
3. Plan the mix. Of the cards you add, roughly 70% should closely fit the profile (`"kind": "fit"`), 20% should explore an interest from "Curious about" or one the user has not seen yet (`"kind": "explore"`), and 10% can be a surprising wildcard (`"kind": "wildcard"`). Respect the hard no-gos always, and never suggest anything listed under "Already own" or a near copy of it (the same product from another shop, or a different colour of it). Products in the same category are fine, for example a different board game.
4. Find products, in this order:
   a. **Favourite websites** from `sources.md`: look for products on those sites that fit the profile and price tiers.
   b. **Instagram gift guides** from `sources.md`: try to read the post or account. Instagram often blocks automated reads. If you cannot read it, tell the user and ask them to paste the caption or drop in a screenshot of the post; then read that. Take the products named in the guide and find each one in a shop where it can be bought. Use the shop's product page and photo, never the creator's photo. Put the Instagram link in the card's `source` as credit.
   c. **Open web search**: gift guides and shops, Dutch shops first, around the profile's interests and price tiers.
   To save the user's Claude usage, delegate page reading to a subagent (Task tool) with a cheaper model where you can, and ask it to return only the card fields. Stop when `cards_to_find` is covered plus 2 or 3 spare. Read each product page once.
5. For every product, check on its own product page: the title, the current price in euros, that it is in stock, and the main image URL (the `og:image` meta tag is usually best). Drop it if any of these cannot be confirmed, or if the price is outside the user's tiers.
6. Write the cards to a temporary JSON file as a list and add them with `python3 app/cli.py add-card --file <tmpfile>`. Each card:
   ```json
   {
     "title": "Hand coffee grinder",
     "shop": "Cookinglife",
     "url": "https://www.example-shop.nl/product-page",
     "price": 54.0,
     "image": "https://www.example-shop.nl/images/grinder.jpg",
     "tags": ["coffee", "kitchen", "practical"],
     "reason": "Because you liked the pour-over kit",
     "kind": "fit",
     "source": "https://www.instagram.com/p/... (or the guide or site where you found it)"
   }
   ```
   Tags: 2 to 4 lowercase words, reusing the words from the profile where they fit, so /learn can count them. The reason is one short line the user sees on the card.
   The script assigns the price tier and refuses duplicates and out-of-range prices; fix or drop whatever it skips.
7. Add one bonus question card per run if there is no unanswered one yet (`unanswered_questions` in the status). Make it a quick, fun question whose answer helps the next scout, for example `{"type": "question", "question": "Tea or coffee?", "options": ["Tea", "Coffee", "Both", "Neither"]}`.
8. Finish with one line per tier saying how many cards were added, and suggest `/feed`.
