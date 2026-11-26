---
description: Update your gift profile from your latest swipes
---

1. Run `python3 app/cli.py stats`. It summarises the swipes since the last /learn: tag scores (on the list +1, imported from their own list +1, already own it +0.7, maybe later +0.3, don't like it -1, removed from the wishlist -0.5) and the gifts the user already owns, price comfort per tier and answers to bonus questions. If there are no new swipes, say so and stop.
2. Update `me/profile.md`, keeping its headings and anything the user wrote by hand:
   - **Likes**: tags with a positive score, strongest first, with the running score in brackets, for example `- cooking (+5)`. Merge with the existing list and add up the scores.
   - **Dislikes**: tags with a clearly negative score. A tag needs at least two dismissals before it becomes a dislike.
   - **Curious about**: drop topics that are now clearly liked or disliked; add one or two related topics the user has not seen yet, so the feed keeps exploring.
   - **Already own**: add every item from `already_owned` (title only), so `/scout` never suggests it or a near copy again.
   - **Bonus answers**: add the new answers.
   Never change a hard no-go, and never move the price tiers in `me/settings.json` yourself. If the stats show the user keeps dismissing the most expensive items in a tier (the average dismissed price is clearly above the average kept price, over at least 5 swipes in that tier), suggest a lower range to the user and only change it with `python3 app/cli.py settings ...` if they agree.
3. Run `python3 app/cli.py mark-learned`.
4. Tell the user in two or three lines what changed in their taste profile, and how full each wishlist tier is now (from `python3 app/cli.py status`).
