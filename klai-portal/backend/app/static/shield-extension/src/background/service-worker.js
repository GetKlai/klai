// Klai Shield service worker, ported from the Superdock extension shape.

const DEFAULT_WEB_BASE = "https://my.getklai.com";

const SK = {
  API_BASE: "klaiShieldApiBase",
  TOKEN: "klaiShieldToken",
  USER: "klaiShieldUser",
  CONFIG: "klaiShieldConfig",
  ENABLED: "klaiShieldEnabled",
  AI_ACT_ENABLED: "klaiShieldComplianceEnabled",
  AI_ACT_LEVEL: "klaiShieldComplianceLevel",
  ACTIVE_COLLECTIONS: "klaiShieldActiveKnowledgeBases",
  ACTIVE_TEMPLATE: "klaiShieldActiveTemplate",
  CACHED_COLLECTIONS: "klaiShieldKnowledgeBases",
  CACHED_TEMPLATES: "klaiShieldTemplates"
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message)
    .then((result) => sendResponse({ ok: true, success: true, result, ...result }))
    .catch((error) => {
      const messageText = error?.message || String(error);
      sendResponse({ ok: false, success: false, error: messageText });
    });
  return true;
});

async function handleMessage(message) {
  const type = normalizeType(message?.type);
  switch (type) {
    case "KLAI_SHIELD_LOGIN":
      return loginWithKlai();
    case "KLAI_SHIELD_LOGOUT":
      return logout();
    case "KLAI_SHIELD_GET_AUTH":
      return getAuth();
    case "KLAI_SHIELD_GET_API_BASE":
      return getBaseInfo();
    case "KLAI_SHIELD_REFRESH_DATA":
    case "KLAI_SHIELD_REFRESH_CONFIG":
      return refreshData();
    case "KLAI_SHIELD_GET_SETTINGS":
      return getSettings();
    case "KLAI_SHIELD_SET_SETTINGS":
      return setSettings(message.settings || {});
    case "KLAI_SHIELD_CHECK_TEXT":
    case "KLAI_SHIELD_CHECK_COMPLIANCE":
      return checkCompliance(message.text || "", message.checkType || "input", message.level || "basic");
    case "KLAI_SHIELD_LOG_EVENT":
    case "KLAI_SHIELD_LOG_CHECK":
      return logCheck(message.payload || message.data || {});
    case "KLAI_SHIELD_QUERY_KNOWLEDGE":
    case "KLAI_SHIELD_QUERY":
      return queryKnowledge(message.kbSlugs || message.collectionIds || null, message.query || "", message.topK || 6);
    case "KLAI_SHIELD_GET_COLLECTIONS":
      return getKnowledgeBases();
    case "KLAI_SHIELD_GET_TEMPLATES":
      return getTemplates();
    case "KLAI_SHIELD_OPEN_SIDEPANEL":
      return { success: true };
    default:
      throw new Error("Onbekend Klai Shield bericht.");
  }
}

function normalizeType(type) {
  if (!type) return "";
  if (type.startsWith("SUPERDOCK_")) {
    return type.replace("SUPERDOCK_", "KLAI_SHIELD_");
  }
  return type;
}

async function getBaseInfo() {
  const settings = await getSettings();
  return { apiBase: settings.apiBase, webBase: settings.apiBase };
}

async function getSettings() {
  const stored = await chrome.storage.local.get(Object.values(SK));
  return {
    apiBase: (stored[SK.API_BASE] || DEFAULT_WEB_BASE).replace(/\/+$/, ""),
    token: stored[SK.TOKEN] || "",
    user: stored[SK.USER] || null,
    config: stored[SK.CONFIG] || null,
    enabled: stored[SK.ENABLED] !== false,
    complianceEnabled: stored[SK.AI_ACT_ENABLED] !== false,
    complianceLevel: stored[SK.AI_ACT_LEVEL] || "basic",
    activeKnowledgeBases: stored[SK.ACTIVE_COLLECTIONS] || [],
    activeTemplate: stored[SK.ACTIVE_TEMPLATE] || "",
    knowledgeBases: stored[SK.CACHED_COLLECTIONS] || [],
    templates: stored[SK.CACHED_TEMPLATES] || []
  };
}

async function setSettings(settings) {
  const patch = {};
  if (typeof settings.apiBase === "string") {
    patch[SK.API_BASE] = settings.apiBase.replace(/\/+$/, "");
  }
  if (typeof settings.enabled === "boolean") patch[SK.ENABLED] = settings.enabled;
  if (typeof settings.complianceEnabled === "boolean") patch[SK.AI_ACT_ENABLED] = settings.complianceEnabled;
  if (typeof settings.complianceLevel === "string") patch[SK.AI_ACT_LEVEL] = settings.complianceLevel;
  if (Array.isArray(settings.activeKnowledgeBases)) patch[SK.ACTIVE_COLLECTIONS] = settings.activeKnowledgeBases;
  if (typeof settings.activeTemplate === "string") patch[SK.ACTIVE_TEMPLATE] = settings.activeTemplate;
  await chrome.storage.local.set(patch);
  return getSettings();
}

async function getAuth() {
  const settings = await getSettings();
  return {
    authenticated: !!settings.token,
    user: settings.user,
    config: settings.config
  };
}

async function loginWithKlai() {
  if (!chrome.identity?.launchWebAuthFlow) {
    throw new Error("Chrome identity API is niet beschikbaar. Laad de extensie unpacked in Chrome.");
  }

  const settings = await getSettings();
  const redirectUri = chrome.identity.getRedirectURL("shield");
  const loginUrl = `${settings.apiBase}/api/app/shield/extension/login?redirect_uri=${encodeURIComponent(redirectUri)}`;
  const finalUrl = await chrome.identity.launchWebAuthFlow({ url: loginUrl, interactive: true });
  if (!finalUrl) throw new Error("Klai login is geannuleerd.");

  const parsed = new URL(finalUrl);
  const error = parsed.searchParams.get("error");
  if (error) {
    throw new Error(error === "platform_admin_required" ? "Je account heeft geen platform-admin toegang." : error);
  }
  const code = parsed.searchParams.get("code");
  if (!code) throw new Error("Klai login gaf geen verificatiecode terug.");

  const exchange = await fetch(`${settings.apiBase}/api/app/shield/extension/exchange`, {
    method: "POST",
    credentials: "omit",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json"
    },
    body: JSON.stringify({ code })
  });
  const body = await parseBody(exchange);
  if (!exchange.ok) {
    throw new Error(errorMessage(body, "Klai login kon niet worden afgerond."));
  }

  await chrome.storage.local.set({
    [SK.TOKEN]: body.token,
    [SK.USER]: body.user,
    [SK.ENABLED]: true,
    [SK.AI_ACT_ENABLED]: true,
    [SK.AI_ACT_LEVEL]: "basic"
  });
  await refreshData();
  const fresh = await getSettings();
  return { success: true, user: fresh.user, config: fresh.config };
}

async function logout() {
  await chrome.storage.local.remove([
    SK.TOKEN,
    SK.USER,
    SK.CONFIG,
    SK.CACHED_COLLECTIONS,
    SK.CACHED_TEMPLATES,
    SK.ACTIVE_COLLECTIONS,
    SK.ACTIVE_TEMPLATE
  ]);
  return { success: true };
}

async function shieldFetch(path, options = {}) {
  const settings = await getSettings();
  if (!settings.token) throw new Error("Log eerst in met je Klai-account.");
  const response = await fetch(`${settings.apiBase}${path}`, {
    ...options,
    credentials: "omit",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${settings.token}`,
      Accept: "application/json",
      ...(options.headers || {})
    }
  });
  const body = await parseBody(response);
  if (response.status === 401) {
    await logout();
    throw new Error("Je Klai sessie in de extensie is verlopen.");
  }
  if (!response.ok) throw new Error(errorMessage(body, "Klai Shield request failed."));
  return body;
}

async function parseBody(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  return response.text();
}

function errorMessage(body, fallback) {
  if (!body) return fallback;
  if (typeof body === "string") return body || fallback;
  return body.detail?.error?.message || body.detail || body.error || body.message || fallback;
}

async function refreshData() {
  const config = await shieldFetch("/api/shield/config");
  const knowledgeBases = config.knowledge_bases || [];
  const existing = await chrome.storage.local.get(SK.ACTIVE_COLLECTIONS);
  const current = existing[SK.ACTIVE_COLLECTIONS] || [];
  const validSlugs = knowledgeBases.map((kb) => kb.slug);
  const active = current.filter((slug) => validSlugs.includes(slug));
  const nextActive = active.length ? active : validSlugs;

  const templates = [
    {
      id: "klai-context",
      name: "Klai instructie",
      description: "Gebruik relevante kenniscontext in je prompt.",
      content: "<knowledge_context>\\n{{context}}\\n</knowledge_context>\\n\\n{{prompt}}",
      built_in: true
    }
  ];

  await chrome.storage.local.set({
    [SK.CONFIG]: config,
    [SK.CACHED_COLLECTIONS]: knowledgeBases,
    [SK.ACTIVE_COLLECTIONS]: nextActive,
    [SK.CACHED_TEMPLATES]: templates
  });
  return { success: true, config, collections: knowledgeBases, templates };
}

async function getKnowledgeBases() {
  const settings = await getSettings();
  if (!settings.knowledgeBases.length && settings.token) await refreshData();
  const fresh = await getSettings();
  return { success: true, collections: fresh.knowledgeBases };
}

async function getTemplates() {
  const settings = await getSettings();
  return { success: true, templates: settings.templates };
}

function normalizeLevel(level) {
  if (level === "basis") return "basic";
  if (level === "uitgebreid") return "extended";
  if (level === "strikt") return "strict";
  return ["basic", "extended", "strict"].includes(level) ? level : "basic";
}

function normalizeStatus(result) {
  const status = result.status || result.score || "green";
  const map = { groen: "green", geel: "yellow", oranje: "orange", rood: "red" };
  return map[status] || status;
}

async function checkCompliance(text, checkType, level) {
  const result = await shieldFetch("/api/shield/check", {
    method: "POST",
    body: JSON.stringify({
      text,
      level: normalizeLevel(level),
      check_type: checkType || "input"
    })
  });
  const status = normalizeStatus(result);
  return {
    ...result,
    status,
    score: { green: "groen", yellow: "geel", orange: "oranje", red: "rood" }[status] || "groen",
    should_block: status === "red",
    should_warn: status === "yellow" || status === "orange"
  };
}

async function logCheck(data) {
  const status = normalizeStatus(data);
  return shieldFetch("/api/shield/log", {
    method: "POST",
    body: JSON.stringify({
      check_type: data.check_type || data.checkType || "input",
      level: normalizeLevel(data.level),
      status,
      risk_score: data.risk_score || data.riskScore || (status === "red" ? 90 : status === "orange" ? 65 : 0),
      text: data.text || data.text_preview || "",
      warnings: data.warnings || [],
      sources: data.sources || [],
      metadata: {
        platform: data.platform || null,
        was_blocked: data.was_blocked || false,
        was_overridden: data.was_overridden || false,
        ...(data.metadata || {})
      },
      surface: "browser_extension"
    })
  }).catch(() => ({ success: false }));
}

async function queryKnowledge(kbSlugs, query, topK) {
  const settings = await getSettings();
  const active = Array.isArray(kbSlugs) && kbSlugs.length ? kbSlugs : settings.activeKnowledgeBases;
  const result = await shieldFetch("/api/shield/query", {
    method: "POST",
    body: JSON.stringify({
      query,
      kb_slugs: active?.length ? active : null,
      top_k: topK || 6
    })
  });
  return { success: true, ...result };
}
