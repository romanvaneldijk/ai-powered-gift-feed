---
description: View, tidy and export your wishlist
argument-hint: "[remove <card id> | check | export]"
---

Arguments: $ARGUMENTS

- **No arguments**: run `python3 app/cli.py wishlist` and show the list grouped by tier (big, medium, small), with how full each tier is compared with the user's targets. Then offer the options below in one line.
- **`remove <id> ...`**: run `python3 app/cli.py remove <id> ...`. This also teaches /learn that the item was not quite right.
- **`check`**: run `python3 app/cli.py check-links`. For every item it reports, open the product page yourself to see whether the product is gone, sold out or changed price. Tell the user what you found and offer to remove dead items.
- **`export`**: create `me/export/wishlist.html`, a simple, good-looking single page that the user can send to family: a section per tier (big wishes, medium gifts, small gifts), each item with its title, shop, price and a link to the shop. Use the shop's product image if the card has one. No personal profile data on this page. Then give the path, and mention they can also paste the list into lootjestrekken.nl.
