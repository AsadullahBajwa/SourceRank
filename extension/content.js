const DEFAULT_SITE_URL = "https://asadullahbajwa.github.io/SourceRank";
const BADGE_CLASS = "sourcerank-score-badge";
const SCANNED_ATTR = "data-sourcerank-scanned";

let scoreMapPromise;
let siteUrlPromise;

function configuredSiteUrl() {
  if (!siteUrlPromise) {
    siteUrlPromise = new Promise(resolve => {
      if (!chrome?.storage?.sync) {
        resolve(DEFAULT_SITE_URL);
        return;
      }
      chrome.storage.sync.get({ siteUrl: DEFAULT_SITE_URL }, items => {
        resolve((items.siteUrl || DEFAULT_SITE_URL).replace(/\/$/, ""));
      });
    });
  }
  return siteUrlPromise;
}

function loadScores() {
  if (!scoreMapPromise) {
    scoreMapPromise = configuredSiteUrl()
      .then(siteUrl => fetch(`${siteUrl}/data/scores.json`))
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

function badgeDetails(row) {
  return [
    row.name || `@${row.handle}`,
    `Accuracy: ${row.accuracy_rate ?? "N/A"}%`,
    `Resolved: ${row.resolved_claims ?? 0}`,
    `Claims: ${row.total_claims ?? 0}`,
  ].join("\n");
}

function attachBadge(link, row) {
  if (!row || link.querySelector(`.${BADGE_CLASS}`)) return;
  const badge = document.createElement("span");
  badge.className = BADGE_CLASS;
  badge.textContent = badgeText(row);
  badge.title = `${badgeDetails(row)}\nOpen SourceRank profile.`;
  badge.dataset.details = badgeDetails(row);
  badge.setAttribute("role", "link");
  badge.setAttribute("tabindex", "0");
  badge.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    configuredSiteUrl().then(siteUrl => {
      window.open(`${siteUrl}/journalist.html?handle=${encodeURIComponent(row.handle)}`, "_blank", "noopener");
    });
  });
  badge.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      badge.click();
    }
  });
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
