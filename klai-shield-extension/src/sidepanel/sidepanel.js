const els = {
  loginOverlay: document.querySelector("#login-overlay"),
  main: document.querySelector("#main-ui"),
  tabBar: document.querySelector("#tab-bar"),
  login: document.querySelector("#login-btn"),
  loginError: document.querySelector("#login-error"),
  logout: document.querySelector("#logout-btn"),
  badge: document.querySelector("#status-badge"),
  collectionList: document.querySelector("#collection-list"),
  templateList: document.querySelector("#template-list"),
  query: document.querySelector("#knowledge-query"),
  runQuery: document.querySelector("#run-query"),
  insertContext: document.querySelector("#insert-context"),
  results: document.querySelector("#knowledge-results"),
  aiToggle: document.querySelector("#sw-aiact"),
  aiCard: document.querySelector("#tc-aiact"),
  checkText: document.querySelector("#check-text"),
  runCheck: document.querySelector("#run-check"),
  checkSummary: document.querySelector("#check-summary"),
  accountName: document.querySelector("#account-name"),
  accountEmail: document.querySelector("#account-email"),
  orgName: document.querySelector("#org-name"),
  orgSub: document.querySelector("#org-sub"),
  apiBase: document.querySelector("#api-base"),
  saveSettings: document.querySelector("#save-settings")
};

let latestChunks = [];
let currentSettings = null;

function runtimeAvailable() {
  return typeof chrome !== "undefined" && !!chrome.runtime?.sendMessage;
}

async function msg(type, payload = {}) {
  if (!runtimeAvailable()) {
    throw new Error("Laad deze map als Chrome extension om in te loggen en te testen.");
  }
  const response = await chrome.runtime.sendMessage({ type, ...payload });
  if (!response?.ok && !response?.success) {
    throw new Error(response?.error || "Klai Shield request failed.");
  }
  return response.result || response;
}

function showLogin(error = "") {
  els.loginOverlay.style.display = "flex";
  els.main.style.display = "none";
  els.tabBar.style.display = "none";
  els.loginError.textContent = error;
}

function showMain() {
  els.loginOverlay.style.display = "none";
  els.main.style.display = "block";
  els.tabBar.style.display = "grid";
}

function updateBadge(text = "Verbonden", state = "ok") {
  els.badge.textContent = text;
  els.badge.dataset.state = state;
}

function statusLabel(status) {
  return {
    green: "Geen risico gevonden",
    yellow: "Controle aanbevolen",
    orange: "Extra beoordeling nodig",
    red: "Geblokkeerd door Shield"
  }[status] || "Onbekende status";
}

async function init() {
  setupTabs();
  bindButton(els.login, login);
  bindButton(els.logout, logout);
  bindButton(els.runQuery, runQuery);
  bindButton(els.insertContext, insertContext);
  bindButton(els.runCheck, runCheck);
  bindButton(els.saveSettings, saveSettings);
  setupComplianceControls();

  if (!runtimeAvailable()) {
    showLogin("Je bekijkt nu het HTML-bestand. Laad de map unpacked in Chrome om Klai-login te testen.");
    return;
  }

  try {
    const auth = await msg("KLAI_SHIELD_GET_AUTH");
    if (!auth?.authenticated) {
      showLogin();
      return;
    }
    showMain();
    await loadAll();
  } catch (error) {
    showLogin(error.message || "Klai Shield kon niet starten.");
  }
}

function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const tabId = button.dataset.tab;
      document.querySelectorAll(".tab-btn").forEach((item) => item.classList.toggle("active", item === button));
      document.querySelectorAll(".panel-section").forEach((section) => {
        section.classList.toggle("active", section.id === tabId);
      });
    });
  });
}

async function login() {
  els.loginError.textContent = "Klai login openen...";
  const result = await msg("KLAI_SHIELD_LOGIN");
  if (!result?.success && !result?.user) throw new Error(result?.error || "Inloggen mislukt.");
  showMain();
  await loadAll();
}

async function logout() {
  await msg("KLAI_SHIELD_LOGOUT");
  showLogin();
}

async function loadAll() {
  updateBadge("Laden...", "");
  await msg("KLAI_SHIELD_REFRESH_DATA").catch(() => {});
  currentSettings = await msg("KLAI_SHIELD_GET_SETTINGS");
  renderAccount();
  renderCollections();
  renderTemplates();
  renderCompliance();
  updateBadge("actief", "ok");
}

function renderAccount() {
  const user = currentSettings.user || currentSettings.config?.user || {};
  const org = currentSettings.config?.organization || {};
  els.accountName.textContent = user.display_name || user.name || user.email || "Klai gebruiker";
  els.accountEmail.textContent = user.email || "";
  els.orgName.textContent = org.name || org.slug || "Klai";
  els.orgSub.textContent = org.platform_admin_only ? "Platform-admin testomgeving" : "Klai organisatie";
  els.apiBase.value = currentSettings.apiBase || "";
}

function renderCollections() {
  const collections = currentSettings.knowledgeBases || currentSettings.config?.knowledge_bases || [];
  const active = currentSettings.activeKnowledgeBases || [];
  if (!collections.length) {
    els.collectionList.innerHTML = '<div class="result-panel empty">Geen kennisbanken gevonden in Klai.</div>';
    return;
  }
  els.collectionList.innerHTML = collections
    .map((kb) => {
      const slug = kb.slug || kb.id;
      const on = active.includes(slug);
      return `
        <div class="toggle-card ${on ? "on" : ""}">
          <div class="t-row">
            <div class="t-icon">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z"/></svg>
            </div>
            <div class="t-body">
              <div class="t-label">${escapeHtml(kb.name || slug || "Klai kennisbank")}</div>
              <div class="t-sub">${escapeHtml(slug || "knowledge")}</div>
            </div>
            <label class="sw"><input type="checkbox" data-kb="${escapeAttr(slug)}" ${on ? "checked" : ""}><span class="sl"></span></label>
          </div>
        </div>
      `;
    })
    .join("");

  els.collectionList.querySelectorAll("input[data-kb]").forEach((checkbox) => {
    checkbox.addEventListener("change", async () => {
      const slug = checkbox.dataset.kb;
      const next = new Set(currentSettings.activeKnowledgeBases || []);
      if (checkbox.checked) next.add(slug);
      else next.delete(slug);
      currentSettings = await msg("KLAI_SHIELD_SET_SETTINGS", {
        settings: { activeKnowledgeBases: Array.from(next) }
      });
      checkbox.closest(".toggle-card").classList.toggle("on", checkbox.checked);
    });
  });
}

function renderTemplates() {
  const templates = currentSettings.templates || [];
  if (!templates.length) {
    els.templateList.innerHTML = '<div class="result-panel empty">Klai gebruikt nu automatisch de standaardinstructie voor context.</div>';
    return;
  }
  els.templateList.innerHTML = templates
    .map((template) => `
      <div class="toggle-card on">
        <div class="t-row">
          <div class="t-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>
          </div>
          <div class="t-body">
            <div class="t-label">${escapeHtml(template.name || "Klai instructie")}</div>
            <div class="t-sub">${escapeHtml(template.description || "Actief voor context-injectie")}</div>
          </div>
          <label class="sw"><input type="checkbox" checked disabled><span class="sl"></span></label>
        </div>
      </div>
    `)
    .join("");
}

function renderCompliance() {
  els.aiToggle.checked = currentSettings.complianceEnabled !== false;
  els.aiCard.classList.toggle("on", els.aiToggle.checked);
  document.querySelectorAll(".lvl").forEach((button) => {
    button.classList.toggle("active", button.dataset.level === (currentSettings.complianceLevel || "basic"));
  });
}

function setupComplianceControls() {
  els.aiToggle.addEventListener("change", async () => {
    els.aiCard.classList.toggle("on", els.aiToggle.checked);
    currentSettings = await msg("KLAI_SHIELD_SET_SETTINGS", {
      settings: { complianceEnabled: els.aiToggle.checked }
    });
  });

  document.querySelectorAll(".lvl").forEach((button) => {
    button.addEventListener("click", async () => {
      document.querySelectorAll(".lvl").forEach((item) => item.classList.toggle("active", item === button));
      currentSettings = await msg("KLAI_SHIELD_SET_SETTINGS", {
        settings: { complianceLevel: button.dataset.level }
      });
    });
  });
}

async function saveSettings() {
  currentSettings = await msg("KLAI_SHIELD_SET_SETTINGS", {
    settings: { apiBase: els.apiBase.value }
  });
  updateBadge("Opgeslagen", "ok");
}

async function runCheck() {
  const text = els.checkText.value.trim();
  if (!text) return;
  els.checkSummary.classList.add("empty");
  els.checkSummary.textContent = "Shield controleert...";
  const result = await msg("KLAI_SHIELD_CHECK_TEXT", {
    text,
    level: currentSettings?.complianceLevel || "basic",
    checkType: "input"
  });
  renderComplianceResult(result);
}

function renderComplianceResult(result) {
  const status = result.status || "green";
  const warnings = result.warnings || [];
  els.checkSummary.classList.remove("empty");
  els.checkSummary.dataset.status = status;
  els.checkSummary.innerHTML = `
    <div class="result-title">
      <span>${escapeHtml(statusLabel(status))}</span>
      <span>${Number(result.risk_score || 0)}/100</span>
    </div>
    ${
      warnings.length
        ? `<ul class="warning-list">${warnings
            .slice(0, 4)
            .map((warning) => `<li>${escapeHtml(warning.label || warning.id || "Waarschuwing")}</li>`)
            .join("")}</ul>`
        : `<p class="empty">Deze tekst kan door volgens de huidige regels.</p>`
    }
  `;
}

async function runQuery() {
  const query = els.query.value.trim();
  if (!query) return;
  els.results.classList.add("empty");
  els.results.textContent = "Klai zoekt in de kennisbank...";
  els.insertContext.disabled = true;
  const result = await msg("KLAI_SHIELD_QUERY_KNOWLEDGE", { query, topK: 6 });
  renderChunks(normalizeChunks(result));
}

function renderChunks(chunks) {
  latestChunks = chunks || [];
  els.insertContext.disabled = latestChunks.length === 0;
  if (!latestChunks.length) {
    els.results.classList.add("empty");
    els.results.textContent = "Geen relevante context gevonden.";
    return;
  }
  els.results.classList.remove("empty");
  els.results.innerHTML = latestChunks
    .slice(0, 6)
    .map((chunk, index) => {
      const title = chunk.title || chunk.source || chunk.metadata?.title || `Bron ${index + 1}`;
      const text = (chunk.text || chunk.content || "").trim().slice(0, 260);
      return `<article class="result"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(text)}</p></article>`;
    })
    .join("");
}

async function insertContext() {
  if (!latestChunks.length) return;
  const context = latestChunks
    .slice(0, 6)
    .map((chunk, index) => {
      const title = chunk.title || chunk.source || chunk.metadata?.title || `Bron ${index + 1}`;
      const text = (chunk.text || chunk.content || "").trim();
      return `[${index + 1}] ${title}\n${text}`;
    })
    .join("\n\n");
  const promptText = `<knowledge_context>\nGebruik deze Klai kennis als context. Verwijs naar bronnen waar relevant.\n\n${context}\n</knowledge_context>`;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  await chrome.tabs.sendMessage(tab.id, {
    type: "KLAI_SHIELD_INSERT_CONTEXT",
    text: promptText
  });
  updateBadge("Ingevoegd", "ok");
}

function normalizeChunks(result) {
  const raw = result?.chunks || result?.results || result?.sources || result?.data || result?.items || [];
  return (Array.isArray(raw) ? raw : [])
    .map((chunk) => ({
      ...chunk,
      text: chunk.text || chunk.content || chunk.chunk || chunk.body || "",
      title: chunk.title || chunk.source_name || chunk.source || chunk.metadata?.title || chunk.metadata?.source || "",
      source: chunk.source || chunk.source_name || chunk.metadata?.source || chunk.metadata?.source_id || ""
    }))
    .filter((chunk) => String(chunk.text || "").trim());
}

function bindButton(button, handler) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await handler();
    } catch (error) {
      if (button === els.login) {
        els.loginError.textContent = error.message || "Inloggen mislukt.";
      } else {
        updateBadge(error.message || "Fout", "error");
      }
    } finally {
      button.disabled = false;
    }
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

init();
