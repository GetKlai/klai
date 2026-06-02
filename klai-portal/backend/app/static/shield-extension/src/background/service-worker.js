const DEFAULT_API_BASE = "https://my.getklai.com";
const STORAGE_KEYS = {
  apiBase: "klaiShieldApiBase",
  token: "klaiShieldToken",
  config: "klaiShieldConfig"
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

async function getSettings() {
  const stored = await chrome.storage.local.get(Object.values(STORAGE_KEYS));
  return {
    apiBase: stored[STORAGE_KEYS.apiBase] || DEFAULT_API_BASE,
    token: stored[STORAGE_KEYS.token] || "",
    config: stored[STORAGE_KEYS.config] || null
  };
}

async function setSettings(settings) {
  const patch = {};
  if (typeof settings.apiBase === "string") {
    patch[STORAGE_KEYS.apiBase] = settings.apiBase.replace(/\/+$/, "");
  }
  if (typeof settings.token === "string") {
    patch[STORAGE_KEYS.token] = settings.token.trim();
  }
  await chrome.storage.local.set(patch);
  return getSettings();
}

async function shieldFetch(path, options = {}) {
  const settings = await getSettings();
  if (!settings.token) {
    throw new Error("Shield token ontbreekt.");
  }
  const base = settings.apiBase.replace(/\/+$/, "");
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${settings.token}`,
      ...(options.headers || {})
    }
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof body === "string" ? body : body?.detail?.error?.message || body?.detail || response.statusText;
    throw new Error(message || "Shield request failed.");
  }
  return body;
}

async function refreshConfig() {
  const config = await shieldFetch("/api/shield/config");
  await chrome.storage.local.set({ [STORAGE_KEYS.config]: config });
  return config;
}

async function handleMessage(message) {
  switch (message?.type) {
    case "KLAI_SHIELD_GET_SETTINGS":
      return getSettings();
    case "KLAI_SHIELD_SET_SETTINGS":
      return setSettings(message.settings || {});
    case "KLAI_SHIELD_REFRESH_CONFIG":
      return refreshConfig();
    case "KLAI_SHIELD_CHECK_TEXT":
      return shieldFetch("/api/shield/check", {
        method: "POST",
        body: JSON.stringify({
          text: message.text || "",
          level: message.level || "basic",
          check_type: message.checkType || "input"
        })
      });
    case "KLAI_SHIELD_QUERY_KNOWLEDGE":
      return shieldFetch("/api/shield/query", {
        method: "POST",
        body: JSON.stringify({
          query: message.query || "",
          kb_slugs: message.kbSlugs || null,
          top_k: message.topK || 6
        })
      });
    case "KLAI_SHIELD_LOG_EVENT":
      return shieldFetch("/api/shield/log", {
        method: "POST",
        body: JSON.stringify(message.payload || {})
      });
    default:
      throw new Error("Onbekend Shield bericht.");
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message)
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});
