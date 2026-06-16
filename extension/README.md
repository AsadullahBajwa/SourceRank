# SourceRank Overlay Prototype

This is a Phase 4 scaffold for a future browser extension that overlays SourceRank context on X.

Current behavior:

- Runs on `x.com` and `twitter.com`.
- Detects visible handle links in the page.
- Shows a small prototype badge with the number of detected handles.

Next implementation steps:

1. Publish `scores.json` at a stable URL.
2. Fetch score data from the static GitHub Pages artifact.
3. Match visible handles to leaderboard rows.
4. Render inline badges beside matching handles.
5. Link badges to `journalist.html?handle=...` profile pages.
