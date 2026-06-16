const SOURCE_RANK_DATA_URL = "https://asadullahbajwa.github.io/SourceRank/data/scores.json";
const BADGE_CLASS = "sourcerank-score-badge";
const SCANNED_ATTR = "data-sourcerank-scanned";

let scoreMapPromise;

function loadScores() {
  if (!scoreMapPromise) {
    scoreMapPromise = fetch(SOURCE_RANK_DATA_URL)
      .then(response => response.ok ? response.json() : { journalists: [] })
      .then(data => new Map(
        (data.journalists || []).map(row => [row.handle.toLowerCase(), row])
      ))
      .catch(() => new Map());
  }
  return scoreMapPromise;
}

function handleFromHref(href) {
  const match = (href || "").match(/^\/([A-Za-z0-9_]{1,30})(?:\/|$)/);
  if (!match) return "";
  const handle = match[1].toLowerCase();
  return ["home", "explore", "notifications", "messages", "settings", "i"].includes(handle)
    ? ""
    : handle;
}

function badgeText(row) {
  if (!row) return "";
  if (row.eligible === false) return "SourceRank N/R";
  return `SourceRank ${row.score}`;
}

function attachBadge(link, row) {
  if (!row || link.querySelector(`.${BADGE_CLASS}`)) return;
  const badge = document.createElement("span");
  badge.className = BADGE_CLASS;
  badge.textContent = badgeText(row);
  badge.title = `${row.name || row.handle}: ${row.rank_status || "ranked"}`;
  link.appendChild(badge);
}

async function scanVisibleHandles() {
  const scores = await loadScores();
  const links = [...document.querySelectorAll(`a[href^="/"]:not([${SCANNED_ATTR}])`)];
  for (const link of links) {
    link.setAttribute(SCANNED_ATTR, "true");
    const handle = handleFromHref(link.getAttribute("href"));
    if (!handle || !scores.has(handle)) continue;
    attachBadge(link, scores.get(handle));
  }
}

const observer = new MutationObserver(() => scanVisibleHandles());
observer.observe(document.documentElement, { childList: true, subtree: true });
scanVisibleHandles();
