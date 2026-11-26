---
description: Play today's swipe game in your browser, then learn from your swipes
---

1. Start `python3 app/feed.py` **in the background** (Bash with `run_in_background`, timeout 30 minutes). It opens today's deck in the user's browser and exits by itself when they finish swiping.
   - If it says there are no new cards, suggest `/scout` and stop.
   - If the browser does not open, give the user the URL that the script printed.
   - Tell the user that for each gift they can pick "Put it on the list" (→), "Kinda, maybe later" (↓, it comes back another day) or "Don't like it" (←), or "already own it" (↑) for things they have already, and that you are finding tomorrow's ideas while they play.
2. While they swipe, scout the next deck: follow `.claude/commands/scout.md` with the argument `next`. Today's deck is fixed once the page opens, so new cards wait for the next `/feed`. Keep it quiet: no questions to the user while they play. If the deck after this one is already full, skip this step.
3. When the feed script has exited, run the steps of the `/learn` command right away (read `.claude/commands/learn.md` and follow it). If scouting is still running, finish it first. Then end with one line: how many new ideas are ready for next time.
