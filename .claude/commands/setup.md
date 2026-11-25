---
description: Play the onboarding quiz and create your gift profile and price tiers
---

You are running the onboarding game for the gift feed. Make it feel like a quick, fun game, not a form: one short question at a time, playful wording, emoji are fine, and it should take about 2 minutes. Use the AskUserQuestion tool for multiple-choice questions where possible, so the user can just pick.

1. Run `python3 app/cli.py init` to create `me/` from the templates (it never overwrites existing files). If `me/profile.md` already has answers, tell the user and ask whether to start over or just update parts.

2. Ask, one by one (keep the order, skip nothing):
   - Who is the list for and what is the occasion: birthday or Christmas, and the date.
   - Age range (under 18, 18-24, 25-34, 35-44, 45-54, 55+).
   - Gender, with "prefer not to say" as an option. It only nudges suggestions, never filters.
   - Three rounds of "this or that" (for example: cosy night in vs. weekend away; something useful vs. something to treat yourself; a gadget vs. something handmade). Use the answers for the gift style.
   - Hobbies and interests: offer around 12 options in two multi-select questions (cooking, coffee, baking, hiking, cycling, running, gaming, board games, reading, music, photography, gardening, travel, fashion, beauty, home decor, tech, sports, art and crafts, films and series) plus "something else", which you follow up on.
   - Hard no-gos: anything they never want (for example clothing, alcohol, perfume, plastic gadgets).
   - The price questions. Ask each as its own question and accept a number in euros:
     a. "What's the least a gift should cost?" (min price)
     b. "Up to what price is a gift a *small* gift for you?" (small max)
     c. "From what price is it a *big* gift?" (big min, must be above small max)
     d. "What's the most anyone should spend on your biggest wish?" (big max)
   - How many gifts they want per tier: big wishes (0 to 3), medium gifts, small gifts.
   Never fill in a price or a count yourself. If the user skips a price question, ask it again later in this session or on the next /setup, but do not guess.

3. Save the prices and counts:
   `python3 app/cli.py settings --min-price A --small-max B --big-min C --big-max D --big N --medium N --small N`
   If it reports an error (for example overlapping tiers), explain it in plain words and ask again.

4. Fill in `me/profile.md` (keep its headings): About me, Hobbies and interests, Hard no-gos. Leave Likes, Dislikes and Curious about for /learn, except put 2 or 3 adjacent interests they did not pick under "Curious about", so the first decks can explore.

5. Show the user `me/sources.md` (their favourite sites and Instagram gift guides) and ask if they want to add or remove any. Mention `/scout add-site <url>` and `/scout add-insta <url>` for later.

6. Finish with a short summary card in the chat: their three price tiers with counts, top interests and no-gos. Then suggest running `/scout` to find their first deck, followed by `/feed`.
