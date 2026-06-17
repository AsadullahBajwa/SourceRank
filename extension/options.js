const DEFAULT_SITE_URL = "https://asadullahbajwa.github.io/SourceRank";
const input = document.getElementById("site-url");
const status = document.getElementById("status");

chrome.storage.sync.get({ siteUrl: DEFAULT_SITE_URL }, items => {
  input.value = items.siteUrl;
});

document.getElementById("save").addEventListener("click", () => {
  const siteUrl = input.value.trim().replace(/\/$/, "") || DEFAULT_SITE_URL;
  chrome.storage.sync.set({ siteUrl }, () => {
    status.textContent = "Saved.";
    setTimeout(() => {
      status.textContent = "";
    }, 1800);
  });
});
