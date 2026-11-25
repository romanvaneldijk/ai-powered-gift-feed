---
description: Play today's swipe game in your browser, then learn from your swipes
---

1. Run `python3 app/feed.py` with a long timeout (30 minutes). It opens today's deck in the user's browser and waits until they finish swiping, then exits by itself.
   - If it says there are no new cards, suggest `/scout` and stop.
   - If the browser does not open, give the user the URL that the script printed.
   - Tell the user, while it runs, that for each gift they can pick "Put it on the list" (→), "Kinda, maybe later" (↓, it comes back another day) or "Don't like it" (←).
2. When the script has exited, run the steps of the `/learn` command right away (read `.claude/commands/learn.md` and follow it).
