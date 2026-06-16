# SourceRank Overlay Prototype

This is a Phase 4 scaffold for a future browser extension that overlays SourceRank context on X.

Current behavior:

- Runs on `x.com` and `twitter.com`.
- Detects visible handle links in the page.
- Fetches the static `scores.json` artifact from GitHub Pages.
- Adds an inline SourceRank badge beside handles that match leaderboard rows.

Next implementation steps:

1. Add an extension settings page for custom leaderboard URLs.
2. Link badges to `journalist.html?handle=...` profile pages.
3. Add hover cards with accuracy, resolved claim count, and verdict totals.
4. Package signed builds for Chrome and Firefox.
