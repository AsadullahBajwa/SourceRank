const DEFAULT_SITE_URL = "https://asadullahbajwa.github.io/SourceRank";

chrome.storage.sync.get({ siteUrl: DEFAULT_SITE_URL }, items => {
  const siteUrl = (items.siteUrl || DEFAULT_SITE_URL).replace(/\/$/, "");
  document.getElementById("site-url").textContent = siteUrl;
  document.getElementById("open-leaderboard").href = `${siteUrl}/index.html`;
});

document.getElementById("open-options").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});
