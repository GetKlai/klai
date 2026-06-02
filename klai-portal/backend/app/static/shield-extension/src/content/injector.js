const COMPOSER_SELECTORS = [
  "textarea",
  "[contenteditable='true']",
  "[role='textbox']"
];
const SEND_BUTTON_SELECTORS = [
  "button[data-testid='send-button']",
  "button[aria-label*='Send']",
  "button[aria-label*='send']",
  "button[aria-label*='Verstuur']",
  "button[type='submit']"
];
const SUPPORTED_LLM_HOSTS = new Set([
  "chatgpt.com",
  "chat.openai.com",
  "claude.ai",
  "gemini.google.com",
  "copilot.microsoft.com",
  "chat.mistral.ai",
  "poe.com",
  "perplexity.ai",
  "www.perplexity.ai"
]);

let suppressNextSubmit = false;
let lastCheck = { text: "", result: null, checkedAt: 0 };

function getComposerText(element) {
  if (!element) return "";
  if (element.tagName === "TEXTAREA" || element.tagName === "INPUT") {
    return element.value || "";
  }
  return element.innerText || element.textContent || "";
}

function findComposer(root = document) {
  const candidates = COMPOSER_SELECTORS.flatMap((selector) => Array.from(root.querySelectorAll(selector)));
  return candidates
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 80 && rect.height > 20 && !element.disabled && element.offsetParent !== null;
    })
    .sort((a, b) => b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom)[0] || null;
}

function findSendButton(root = document) {
  return SEND_BUTTON_SELECTORS.flatMap((selector) => Array.from(root.querySelectorAll(selector)))
    .filter((button) => !button.disabled && button.offsetParent !== null)
    .sort((a, b) => b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom)[0] || null;
}

function isSupportedLlmHost() {
  return SUPPORTED_LLM_HOSTS.has(window.location.hostname);
}

function sendRuntimeMessage(payload) {
  return chrome.runtime.sendMessage(payload).then((response) => {
    if (!response?.ok && !response?.success) {
      throw new Error(response?.error || "Shield request failed.");
    }
    return response.result || response;
  });
}

function showNotice(kind, text, warnings = []) {
  let notice = document.querySelector(".klai-shield-notice");
  if (!notice) {
    notice = document.createElement("div");
    notice.className = "klai-shield-notice";
    document.body.appendChild(notice);
  }
  notice.dataset.kind = kind;
  notice.innerHTML = `
    <div class="klai-shield-notice__title">${kind === "blocked" ? "Klai Shield blokkeerde deze prompt" : "Klai Shield waarschuwing"}</div>
    <div class="klai-shield-notice__body">${text}</div>
    ${warnings.length ? `<ul>${warnings.slice(0, 3).map((w) => `<li>${w.label || w.id}</li>`).join("")}</ul>` : ""}
  `;
  window.clearTimeout(notice._hideTimer);
  notice._hideTimer = window.setTimeout(() => notice.remove(), kind === "blocked" ? 9000 : 5000);
}

async function getSettings() {
  return sendRuntimeMessage({ type: "KLAI_SHIELD_GET_SETTINGS" });
}

async function checkText(text, level) {
  if (!text.trim()) return { status: "green", should_block: false, warnings: [] };
  const now = Date.now();
  if (lastCheck.text === text && lastCheck.result && now - lastCheck.checkedAt < 15000) {
    return lastCheck.result;
  }
  const result = await sendRuntimeMessage({
    type: "KLAI_SHIELD_CHECK_TEXT",
    text,
    level: level || "basic",
    checkType: "input"
  });
  lastCheck = { text, result, checkedAt: now };
  return result;
}

async function logResult(text, result) {
  return sendRuntimeMessage({
    type: "KLAI_SHIELD_LOG_EVENT",
    payload: {
      check_type: "input",
      level: result.level || "basic",
      status: result.status || "green",
      risk_score: result.risk_score || 0,
      text,
      warnings: result.warnings || [],
      sources: [],
      metadata: { page_url: location.href, page_title: document.title },
      surface: "browser_extension"
    }
  }).catch(() => {});
}

async function guardAndSubmit(submit) {
  const composer = findComposer();
  const text = getComposerText(composer);
  if (!text.trim()) return;

  let settings;
  try {
    settings = await getSettings();
  } catch {
    submit();
    return;
  }
  if (!settings.token || settings.enabled === false || settings.complianceEnabled === false) {
    submit();
    return;
  }

  let result;
  try {
    result = await checkText(text, settings.complianceLevel || "basic");
  } catch (error) {
    showNotice("warning", error.message || "Klai Shield kon deze prompt niet controleren.");
    submit();
    return;
  }

  await logResult(text, result);
  if (result.should_block) {
    showNotice("blocked", "Verwijder gevoelige of verboden informatie voordat je verdergaat.", result.warnings || []);
    return;
  }
  if (result.should_warn) {
    showNotice("warning", "Controleer deze prompt extra zorgvuldig.", result.warnings || []);
  }
  suppressNextSubmit = true;
  submit();
}

function insertTextIntoComposer(text) {
  const composer = findComposer();
  if (!composer) {
    showNotice("warning", "Geen actieve prompt-editor gevonden.");
    return;
  }
  const prefix = getComposerText(composer).trim() ? "\n\n" : "";
  const value = `${prefix}${text}`;
  composer.focus();
  if (composer.tagName === "TEXTAREA" || composer.tagName === "INPUT") {
    const start = composer.selectionStart ?? composer.value.length;
    const end = composer.selectionEnd ?? composer.value.length;
    composer.value = `${composer.value.slice(0, start)}${value}${composer.value.slice(end)}`;
    composer.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }
  document.execCommand("insertText", false, value);
}

if (isSupportedLlmHost()) {
  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Enter" || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return;
      const composer = event.target?.closest?.(COMPOSER_SELECTORS.join(","));
      if (!composer) return;
      if (suppressNextSubmit) {
        suppressNextSubmit = false;
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      guardAndSubmit(() => {
        const button = findSendButton();
        if (button) button.click();
      });
    },
    true
  );

  document.addEventListener(
    "click",
    (event) => {
      const button = event.target?.closest?.(SEND_BUTTON_SELECTORS.join(","));
      if (!button) return;
      if (suppressNextSubmit) {
        suppressNextSubmit = false;
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      guardAndSubmit(() => button.click());
    },
    true
  );
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "KLAI_SHIELD_INSERT_CONTEXT") return false;
  insertTextIntoComposer(message.text || "");
  sendResponse({ ok: true });
  return true;
});
