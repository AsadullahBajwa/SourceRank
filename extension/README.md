# SourceRank Overlay Prototype

This is a Phase 4 scaffold for a future browser extension that overlays SourceRank context on X.

Current behavior:

- Runs on `x.com` and `twitter.com`.
- Detects visible handle links in the page.
- Fetches the static `scores.json` artifact from GitHub Pages.
- Adds an inline SourceRank badge beside handles that match leaderboard rows.
- Opens the matching SourceRank profile when a badge is clicked.

Next implementation steps:

1. Add an extension settings page for custom leaderboard URLs.
2. Add hover cards with accuracy, resolved claim count, and verdict totals.
3. Package signed builds for Chrome and Firefox.
