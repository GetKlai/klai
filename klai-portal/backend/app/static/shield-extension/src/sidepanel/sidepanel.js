const els = {
  apiBase: document.querySelector("#api-base"),
  token: document.querySelector("#token"),
  save: document.querySelector("#save-settings"),
  refresh: document.querySelector("#refresh-config"),
  status: document.querySelector("#status-pill"),
  checkText: document.querySelector("#check-text"),
  runCheck: document.querySelector("#run-check"),
  checkResult: document.querySelector("#check-result"),
  knowledgeQuery: document.querySelector("#knowledge-query"),
  runQuery: document.querySelector("#run-query"),
  knowledgeResults: document.querySelector("#knowledge-results"),
  insertContext: document.querySelector("#insert-context")
};

let latestChunks = [];

function setStatus(text, state = "") {
  els.status.textContent = text;
  if (state) {
    els.status.dataset.state = state;
  } else {
    delete els.status.dataset.state;
  }
}

async function send(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (!response?.ok) {
    throw new Error(response?.error || "Shield request failed.");
  }
  return response.result;
}

async function loadSettings() {
  const settings = await send({ type: "KLAI_SHIELD_GET_SETTINGS" });
  els.apiBase.value = settings.apiBase || "";
  els.token.value = settings.token || "";
  if (settings.config) {
    setStatus(settings.config.organization?.slug || "Verbonden", "ok");
  }
}

async function saveSettings() {
  await send({
    type: "KLAI_SHIELD_SET_SETTINGS",
    settings: {
      apiBase: els.apiBase.value,
      token: els.token.value
    }
  });
  setStatus("Opgeslagen");
}

async function refreshConfig() {
  const config = await send({ type: "KLAI_SHIELD_REFRESH_CONFIG" });
  setStatus(config.organization?.slug || "Verbonden", "ok");
}

async function runCheck() {
  const text = els.checkText.value.trim();
  if (!text) return;
  const result = await send({
    type: "KLAI_SHIELD_CHECK_TEXT",
    text,
    level: "basic",
    checkType: "input"
  });
  els.checkResult.textContent = JSON.stringify(result, null, 2);
}

function renderChunks(chunks) {
  latestChunks = chunks || [];
  els.insertContext.disabled = latestChunks.length === 0;
  els.knowledgeResults.innerHTML = latestChunks
    .slice(0, 6)
    .map((chunk) => {
      const title = chunk.title || chunk.metadata?.title || "Klai bron";
      const text = (chunk.text || chunk.content || "").trim().slice(0, 240);
      return `<article class="result"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(text)}</p></article>`;
    })
    .join("");
}

async function runQuery() {
  const query = els.knowledgeQuery.value.trim();
  if (!query) return;
  const result = await send({
    type: "KLAI_SHIELD_QUERY_KNOWLEDGE",
    query,
    topK: 6
  });
  renderChunks(result.chunks || []);
}

async function insertContext() {
  const context = latestChunks
    .slice(0, 6)
    .map((chunk, index) => {
      const title = chunk.title || chunk.metadata?.title || `Bron ${index + 1}`;
      const text = (chunk.text || chunk.content || "").trim();
      return `[${index + 1}] ${title}\n${text}`;
    })
    .join("\n\n");
  const promptText = `Klai context:\n${context}\n\nGebruik deze context alleen waar relevant.`;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  await chrome.tabs.sendMessage(tab.id, {
    type: "KLAI_SHIELD_INSERT_CONTEXT",
    text: promptText
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bind(button, handler) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await handler();
    } catch (error) {
      setStatus(error.message || "Fout", "error");
    } finally {
      button.disabled = false;
    }
  });
}

bind(els.save, saveSettings);
bind(els.refresh, refreshConfig);
bind(els.runCheck, runCheck);
bind(els.runQuery, runQuery);
bind(els.insertContext, insertContext);
loadSettings().catch((error) => setStatus(error.message || "Fout", "error"));
