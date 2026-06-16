const SOURCE_RANK_BADGE_CLASS = "sourcerank-overlay-badge";

function visibleHandles() {
  return [...document.querySelectorAll('a[href^="/"]')]
    .map(link => (link.getAttribute("href") || "").match(/^\/([A-Za-z0-9_]{1,30})(?:\/|$)/))
    .filter(Boolean)
    .map(match => match[1])
    .filter(handle => !["home", "explore", "notifications", "messages", "settings"].includes(handle.toLowerCase()));
}

function attachPrototypeBadge() {
  if (document.querySelector(`.${SOURCE_RANK_BADGE_CLASS}`)) return;
  const handles = visibleHandles();
  if (!handles.length) return;

  const badge = document.createElement("div");
  badge.className = SOURCE_RANK_BADGE_CLASS;
  badge.textContent = `SourceRank prototype: ${handles.length} handle(s) detected`;
  document.body.appendChild(badge);
}

const observer = new MutationObserver(() => attachPrototypeBadge());
observer.observe(document.documentElement, { childList: true, subtree: true });
attachPrototypeBadge();
