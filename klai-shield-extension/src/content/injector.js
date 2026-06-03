// Klai Shield content script.
// Mirrors the TalkWithData Shield flow: mention insert, prompt enrichment, pre-flight checks, and page badge.

(function () {
  "use strict";

  const SK = {
    ENABLED: "klaiShieldEnabled",
    AI_ACT_ENABLED: "klaiShieldComplianceEnabled",
    AI_ACT_LEVEL: "klaiShieldComplianceLevel",
    ACTIVE_COLLECTIONS: "klaiShieldActiveKnowledgeBases",
    CACHED_TEMPLATES: "klaiShieldTemplates",
    CACHED_COLLECTIONS: "klaiShieldKnowledgeBases",
    ACTIVE_TEMPLATE: "klaiShieldActiveTemplate"
  };

  const AI_ACT_CHECKS = [
    {
      id: "social_scoring",
      level: "strict",
      severity: "red",
      article: "Art. 5(1)(c)",
      label: "Social scoring",
      description: "Deze prompt lijkt mensen te scoren of classificeren op sociaal gedrag.",
      keywords: [
        "social score",
        "social scoring",
        "burgers scoren",
        "mensen classificeren",
        "gedragsscore",
        "citizen score",
        "score people based on behavior"
      ]
    },
    {
      id: "biometric",
      level: "strict",
      severity: "red",
      article: "Art. 5(1)(d)",
      label: "Biometrische identificatie",
      description: "Deze prompt lijkt real-time biometrische identificatie in publieke ruimtes te vragen.",
      keywords: [
        "gezichtsherkenning openbaar",
        "facial recognition public",
        "biometrische surveillance",
        "real-time identificatie",
        "mass surveillance",
        "biometric identification public"
      ]
    },
    {
      id: "manipulation",
      level: "strict",
      severity: "red",
      article: "Art. 5(1)(a)",
      label: "Manipulatie van kwetsbare groepen",
      description: "Deze prompt lijkt gericht op manipulatie van kwetsbare personen.",
      keywords: [
        "kinderen manipuleren",
        "ouderen misleiden",
        "kwetsbare",
        "manipulate children",
        "exploit elderly",
        "subliminal technique",
        "dark pattern kinderen"
      ]
    },
    {
      id: "high_risk",
      level: "extended",
      severity: "orange",
      article: "Art. 6 & Annex III",
      label: "Hoog-risico domein",
      description: "Dit onderwerp valt mogelijk in een hoog-risico AI domein. Menselijke controle blijft nodig.",
      keywords: [
        "diagnose",
        "diagnosis",
        "medisch advies",
        "medical advice",
        "juridisch advies",
        "legal advice",
        "financieel advies",
        "financial advice",
        "kredietbeoordeling",
        "credit scoring",
        "cv screenen",
        "recruitment ai",
        "strafrechtelijk",
        "asiel",
        "verzekering beoordelen"
      ]
    },
    {
      id: "transparency",
      level: "basic",
      severity: "orange",
      article: "Art. 52(1)",
      label: "Transparantie",
      description: "De prompt vraagt mogelijk om te verbergen dat AI wordt gebruikt.",
      keywords: [
        "doe alsof je een mens bent",
        "pretend to be human",
        "act as a real person",
        "do not mention you are ai",
        "verberg dat je ai bent",
        "fake review",
        "nep review"
      ]
    },
    {
      id: "deepfake",
      level: "basic",
      severity: "orange",
      article: "Art. 52(3)",
      label: "Synthetische content",
      description: "Synthetische of gemanipuleerde content moet herkenbaar blijven.",
      keywords: [
        "deepfake",
        "nep video",
        "fake image of",
        "face swap",
        "voice clone",
        "stem klonen",
        "nep audio"
      ]
    },
    {
      id: "emotion",
      level: "extended",
      severity: "orange",
      article: "Art. 5(1)(f)",
      label: "Emotieherkenning",
      description: "Emotieherkenning in werk of onderwijs is beperkt onder de AI Act.",
      keywords: [
        "emotieherkenning",
        "emotion recognition",
        "emotion detection",
        "sentiment werknemer",
        "emotie meten werkplek",
        "emotion ai workplace",
        "emotion school"
      ]
    },
    {
      id: "pii",
      level: "basic",
      severity: "red",
      article: "GDPR & Art. 10",
      label: "Persoonsgegevens",
      description: "Deze prompt bevat mogelijk persoonsgegevens. Minimaliseer data voordat je verstuurt.",
      patterns: [
        /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/,
        /\b(?:\+31|0031|0)\s?[1-9](?:[\s.-]?[0-9]){8}\b/,
        /\b\d{4}\s?[A-Z]{2}\b/,
        /\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b/,
        /\bNL\d{2}\s?[A-Z]{4}\s?\d{4}\s?\d{4}\s?\d{2}\b/i,
        /\b[A-Z]{2}\d{2}\s?[A-Z]{4}\s?[\d\s]{10,26}\b/
      ],
      bsnPattern: /\b(\d{9})\b/g
    }
  ];

  const KLAI_MARK_PATH = "M0 17.5666C0 27.267 7.86018 36.5436 17.5605 36.5436C27.2609 36.5436 36.5434 27.267 36.5434 17.5666C36.5434 7.86625 27.2609 0.006073 17.5605 0.006073C7.86018 0.006073 0 7.86625 0 17.5666ZM0 87.0532C0 97.0109 7.86018 105.08 17.5605 105.08C27.2609 105.08 35.5283 97.0109 35.5283 87.0532C35.5283 77.0954 27.2609 69.0266 17.5605 69.0266C7.86018 69.0266 0 77.0954 0 87.0532ZM84.2465 70.0417C79.8826 70.2126 78.4675 68.8677 75.3787 65.7788L74.9186 65.3188C68.4986 58.8988 68.492 46.4895 74.9055 40.0624L75.3787 39.5892C78.4675 36.5003 82.7197 34.9427 87.0835 35.107C87.9247 35.1399 88.7791 35.107 89.6466 35.0216C97.4805 34.2001 104.664 26.8065 105.268 18.9529C106.096 8.16819 97.1782 -0.763238 86.3935 0.0516971C78.7567 0.630038 71.0604 5.60564 70.0352 13.1964C69.9169 14.0573 70.1933 16.863 70.1999 17.6977C70.21 18.9838 70.0497 20.1666 69.7607 21.2772C67.7771 28.8978 60.8887 34.7174 53.0205 35.0334C52.1494 35.0684 51.231 35.1296 50.2471 35.2648C42.3212 36.3426 35.3451 41.7876 34.5104 49.7398C33.4063 60.288 42.3541 70.2215 52.6788 70.2215H52.7379C56.9112 70.2084 60.9333 71.7265 63.8841 74.6774L65.7112 76.5044C71.5078 82.3011 67.9724 92.9393 73.4533 99.0354C76.577 102.51 81.3815 104.923 86.4 105.303C97.1848 106.118 106.103 97.1867 105.275 86.402C104.67 78.5484 99.1861 70.8632 91.3522 70.0417C88.9415 69.5667 86.6945 69.9461 84.2465 70.0417Z";

  function isContextValid() {
    try {
      return !!chrome.runtime?.id;
    } catch (_) {
      return false;
    }
  }

  function withTimeout(promise, ms) {
    return Promise.race([
      promise,
      new Promise((_, reject) => setTimeout(() => reject(new Error("Timeout")), ms))
    ]);
  }

  function runtimeMessage(type, payload = {}) {
    return new Promise((resolve, reject) => {
      if (!isContextValid()) {
        reject(new Error("Klai Shield context is niet beschikbaar."));
        return;
      }
      chrome.runtime.sendMessage({ type, ...payload }, (response) => {
        const lastError = chrome.runtime.lastError;
        if (lastError) {
          reject(new Error(lastError.message));
          return;
        }
        if (!response?.ok && !response?.success) {
          reject(new Error(response?.error || "Klai Shield request failed."));
          return;
        }
        resolve(response.result || response);
      });
    });
  }

  function esc(value) {
    const div = document.createElement("div");
    div.textContent = String(value ?? "");
    return div.innerHTML;
  }

  function toArray(value) {
    if (Array.isArray(value)) return value;
    if (Array.isArray(value?.data)) return value.data;
    if (Array.isArray(value?.items)) return value.items;
    if (value?.built_in || value?.custom) return [...(value.built_in || []), ...(value.custom || [])];
    return [];
  }

  function normalizeLevel(level) {
    if (level === "basis") return "basic";
    if (level === "uitgebreid") return "extended";
    if (level === "strikt") return "strict";
    return ["basic", "extended", "strict"].includes(level) ? level : "basic";
  }

  function normalizeStatus(value) {
    const status = value?.status || value?.score || value || "green";
    const map = {
      groen: "green",
      geel: "yellow",
      oranje: "orange",
      rood: "red",
      green: "green",
      yellow: "yellow",
      orange: "orange",
      red: "red"
    };
    return map[String(status).toLowerCase()] || "green";
  }

  function normalizeSeverity(value) {
    return normalizeStatus(value || "green");
  }

  function bsnElfproef(digits) {
    if (digits.length !== 9) return false;
    const d = digits.split("").map(Number);
    const sum = d[0] * 9 + d[1] * 8 + d[2] * 7 + d[3] * 6 + d[4] * 5 + d[5] * 4 + d[6] * 3 + d[7] * 2 + d[8] * -1;
    return sum > 0 && sum % 11 === 0;
  }

  function cleanSourceName(raw) {
    return String(raw || "")
      .replace(/^url_/i, "")
      .replace(/_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i, "")
      .replace(/\.(txt|pdf|docx?|md|html?)$/i, "")
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function cleanChunkText(text) {
    return String(text || "")
      .replace(/\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g, "")
      .replace(/\b(?:\+31|0031|0)\s?[1-9][\s.-]?(?:[0-9][\s.-]?){7,8}\b/g, "")
      .replace(/https?:\/\/\S+/g, "")
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && (line.length >= 18 || line.includes(".") || line.includes(",")))
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function truncateAtSentence(text, maxLength) {
    const value = String(text || "").trim();
    if (value.length <= maxLength) return value;
    const cut = value.slice(0, maxLength);
    const lastStop = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf(".\n"), cut.lastIndexOf("? "), cut.lastIndexOf("! "));
    return `${lastStop > 180 ? cut.slice(0, lastStop + 1) : cut.trim()}...`;
  }

  function detectSite() {
    const h = window.location.hostname;
    if (h === "chatgpt.com" || h === "chat.openai.com") return { key: "chatgpt", name: "ChatGPT" };
    if (h === "claude.ai") return { key: "claude", name: "Claude" };
    if (h === "gemini.google.com") return { key: "gemini", name: "Gemini" };
    if (h === "copilot.microsoft.com") return { key: "copilot", name: "Copilot" };
    if (h === "chat.mistral.ai") return { key: "mistral", name: "Mistral" };
    if (h === "poe.com") return { key: "poe", name: "Poe" };
    if (h === "perplexity.ai" || h === "www.perplexity.ai") return { key: "perplexity", name: "Perplexity" };
    return null;
  }

  function getTextarea() {
    return (
      document.querySelector("#prompt-textarea") ||
      document.querySelector('div.ProseMirror[contenteditable="true"]') ||
      document.querySelector('[contenteditable="true"].ProseMirror') ||
      document.querySelector('fieldset [contenteditable="true"]') ||
      document.querySelector('rich-textarea div[contenteditable="true"]') ||
      document.querySelector("#searchbox") ||
      document.querySelector('textarea[name="q"]') ||
      document.querySelector("textarea[placeholder]") ||
      document.querySelector('.ql-editor[contenteditable="true"]') ||
      document.querySelector('div[contenteditable="true"][data-placeholder]') ||
      document.querySelector('div[role="textbox"][contenteditable="true"]') ||
      document.querySelector('div[contenteditable="true"]')
    );
  }

  function getSubmitButton() {
    return (
      document.querySelector('button[data-testid="send-button"]') ||
      document.querySelector('button[aria-label="Send prompt"]') ||
      document.querySelector('button[aria-label="Send Message"]') ||
      document.querySelector('button[aria-label="Send message"]') ||
      document.querySelector('button[aria-label="Send"]') ||
      document.querySelector('button[data-testid="send-message"]') ||
      document.querySelector('fieldset button[type="button"]:last-child') ||
      document.querySelector('fieldset button:not([disabled])') ||
      document.querySelector("button.send-button") ||
      document.querySelector('mat-icon[data-mat-icon-name="send"]')?.closest("button") ||
      document.querySelector('button[aria-label="Submit"]') ||
      document.querySelector('button[aria-label="Verzenden"]') ||
      document.querySelector('button[aria-label="Ask"]') ||
      document.querySelector('button[aria-label="Bericht verzenden"]') ||
      document.querySelector('form button[type="submit"]')
    );
  }

  function isEditable(el) {
    if (!el) return false;
    if (el.tagName === "TEXTAREA") return true;
    if (el.tagName === "INPUT") {
      const type = (el.type || "").toLowerCase();
      return ["text", "search", "email", "url", "tel", ""].includes(type);
    }
    return !!el.isContentEditable;
  }

  function getText(el) {
    if (!el) return "";
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") return el.value || "";
    return el.innerText || el.textContent || "";
  }

  function getCaretPos(el) {
    if (!el) return 0;
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") return el.selectionStart ?? getText(el).length;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return getText(el).length;
    const range = sel.getRangeAt(0);
    const pre = range.cloneRange();
    pre.selectNodeContents(el);
    try {
      pre.setEnd(range.endContainer, range.endOffset);
      return pre.toString().length;
    } catch (_) {
      return getText(el).length;
    }
  }

  function setText(el, text) {
    if (!el) return;
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) setter.call(el, text);
      else el.value = text;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    el.focus();
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);

    try {
      document.execCommand("insertText", false, text);
      if (getText(el).trim() === text.trim()) {
        el.dispatchEvent(new Event("input", { bubbles: true }));
        return;
      }
    } catch (_) {}

    el.innerHTML = text
      .split("\n")
      .map((line) => `<p>${line ? esc(line) : "<br>"}</p>`)
      .join("");
    try {
      el.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertReplacementText", data: text }));
    } catch (_) {}
    try {
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    } catch (_) {
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setCaret(el, pos) {
    if (!el) return;
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      try {
        el.setSelectionRange(pos, pos);
      } catch (_) {}
    }
    try {
      el.focus();
    } catch (_) {}
  }

  async function getSettings() {
    try {
      return await runtimeMessage("KLAI_SHIELD_GET_SETTINGS");
    } catch (_) {
      const data = await chrome.storage.local.get(Object.values(SK));
      return {
        enabled: data[SK.ENABLED] !== false,
        complianceEnabled: data[SK.AI_ACT_ENABLED] !== false,
        complianceLevel: data[SK.AI_ACT_LEVEL] || "basic",
        activeKnowledgeBases: data[SK.ACTIVE_COLLECTIONS] || [],
        activeTemplate: data[SK.ACTIVE_TEMPLATE] || "",
        knowledgeBases: data[SK.CACHED_COLLECTIONS] || [],
        templates: data[SK.CACHED_TEMPLATES] || []
      };
    }
  }

  function normalizeChunks(result) {
    const raw = result?.chunks || result?.results || result?.sources || result?.data || result?.items || [];
    return toArray(raw)
      .map((chunk) => ({
        ...chunk,
        text: chunk.text || chunk.content || chunk.chunk || chunk.body || "",
        title: chunk.title || chunk.source_name || chunk.source || chunk.metadata?.title || chunk.metadata?.source || "",
        source: chunk.source || chunk.source_name || chunk.metadata?.source || chunk.metadata?.source_id || "",
        kbSlug: chunk.kb_slug || chunk.knowledge_base_slug || chunk.collection_slug || chunk.metadata?.kb_slug || ""
      }))
      .filter((chunk) => String(chunk.text || "").trim());
  }

  async function queryKnowledge(query, topK = 6, kbSlugs = null) {
    const result = await withTimeout(
      runtimeMessage("KLAI_SHIELD_QUERY_KNOWLEDGE", { query, topK, kbSlugs }),
      15000
    );
    return { ...result, chunks: normalizeChunks(result) };
  }

  function formatChunksForInsert(chunks, options = {}) {
    const top = normalizeChunks({ chunks }).slice(0, options.limit || 6);
    if (!top.length) return "";
    const body = top
      .map((chunk, index) => {
        const source = cleanSourceName(chunk.title || chunk.source) || `Bron ${index + 1}`;
        const text = truncateAtSentence(cleanChunkText(chunk.text), options.compact ? 700 : 1200);
        return text ? `[${index + 1}] ${source}\n${text}` : "";
      })
      .filter(Boolean)
      .join("\n\n");
    if (!body) return "";
    return `<knowledge_context>\nGebruik deze Klai kennis als context. Verwijs naar bronnen waar relevant.\n\n${body}\n</knowledge_context>`;
  }

  async function enrichPrompt(userPrompt, settings) {
    const activeKnowledgeBases = settings?.activeKnowledgeBases || [];
    const enrichments = [];
    const parts = [];
    let ragChunks = [];

    const templates = toArray(settings?.templates);
    const activeTemplate = settings?.activeTemplate;
    const template = activeTemplate ? templates.find((item) => String(item.id) === String(activeTemplate)) : null;

    if (template) {
      const content = template.template || template.content || "";
      if (content && !content.includes("{{context}}")) {
        if (/\{\{\s*prompt\s*\}\}/i.test(content)) parts.push(content.replace(/\{\{\s*prompt\s*\}\}/gi, userPrompt));
        else parts.push(content);
        enrichments.push({ type: "template", label: template.name || "Klai instructie" });
      }
    }

    if (activeKnowledgeBases.length > 0 && userPrompt.trim().length > 5) {
      updateLoading("Klai zoekt relevante kennis...");
      try {
        const result = await queryKnowledge(userPrompt, 6, activeKnowledgeBases);
        ragChunks = result.chunks || [];
      } catch (error) {
        console.warn("[Klai Shield] Knowledge query failed:", error.message);
      }
      const context = formatChunksForInsert(ragChunks);
      if (context) {
        if (template && (template.content || template.template || "").includes("{{context}}")) {
          const rawTemplate = template.content || template.template || "";
          parts.push(
            rawTemplate
              .replace(/\{\{\s*context\s*\}\}/gi, context.replace(/^<knowledge_context>\n?|\n?<\/knowledge_context>$/g, ""))
              .replace(/\{\{\s*prompt\s*\}\}/gi, userPrompt)
          );
          enrichments.push({ type: "template", label: template.name || "Klai instructie" });
        } else {
          parts.push(context);
        }
        enrichments.push({
          type: "rag",
          label: `${ragChunks.length} bron${ragChunks.length === 1 ? "" : "nen"}`,
          chunks: ragChunks
        });
      }
    }

    if (parts.length > 0 && !parts.some((part) => part.includes(userPrompt))) parts.push(userPrompt);
    return {
      enrichedPrompt: parts.length > 0 ? parts.join("\n\n") : userPrompt,
      enrichments,
      ragChunks
    };
  }

  async function checkAiAct(text, settings) {
    if (!settings || settings.complianceEnabled === false) return { status: "green", score: "green", warnings: [] };
    const level = normalizeLevel(settings.complianceLevel || "basic");
    const levels = ["basic"];
    if (level === "extended" || level === "strict") levels.push("extended");
    if (level === "strict") levels.push("strict");
    const textLower = text.toLowerCase();
    const warnings = [];

    for (const check of AI_ACT_CHECKS) {
      if (!levels.includes(check.level)) continue;
      let hit = false;
      if (check.keywords) hit = check.keywords.some((keyword) => textLower.includes(keyword.toLowerCase()));
      if (!hit && check.patterns) hit = check.patterns.some((pattern) => pattern.test(text));
      if (!hit && check.bsnPattern) {
        check.bsnPattern.lastIndex = 0;
        let match;
        while ((match = check.bsnPattern.exec(text)) !== null) {
          if (bsnElfproef(match[1])) {
            hit = true;
            break;
          }
        }
      }
      if (hit) warnings.push({ ...check, source: "client" });
    }

    if (text.length > 20) {
      try {
        const serverResult = await withTimeout(
          runtimeMessage("KLAI_SHIELD_CHECK_COMPLIANCE", {
            text: text.substring(0, 4000),
            checkType: "input",
            level
          }),
          9000
        );
        for (const warning of serverResult?.warnings || []) {
          const next = {
            ...warning,
            severity: normalizeSeverity(warning.severity || warning.status || serverResult.status),
            source: "server"
          };
          if (!warnings.some((existing) => existing.article === next.article && existing.label === next.label)) {
            warnings.push(next);
          }
        }
        const serverStatus = normalizeStatus(serverResult);
        if (serverStatus !== "green" && !warnings.length) {
          warnings.push({
            severity: serverStatus,
            label: "Klai Shield waarschuwing",
            description: serverResult?.summary || serverResult?.message || "Klai adviseert deze prompt te beoordelen.",
            source: "server"
          });
        }
      } catch (error) {
        console.warn("[Klai Shield] Server compliance check failed:", error.message);
      }
    }

    let status = "green";
    if (warnings.some((warning) => normalizeSeverity(warning.severity) === "red")) status = "red";
    else if (warnings.some((warning) => normalizeSeverity(warning.severity) === "orange")) status = "orange";
    else if (warnings.some((warning) => normalizeSeverity(warning.severity) === "yellow")) status = "yellow";

    return {
      status,
      score: status,
      warnings,
      should_block: status === "red",
      should_warn: status === "yellow" || status === "orange"
    };
  }

  function showLoading(message) {
    hideLoading();
    const el = document.createElement("div");
    el.id = "klai-shield-loading";
    el.innerHTML = `
      <div class="klai-loading-backdrop"></div>
      <div class="klai-loading-card">
        <span class="klai-loading-mark">${markSvg(16)}</span>
        <span class="klai-loading-text">${esc(message)}</span>
      </div>
    `;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add("is-visible"));
  }

  function updateLoading(message) {
    const el = document.querySelector(".klai-loading-text");
    if (el) el.textContent = message;
  }

  function hideLoading() {
    const el = document.getElementById("klai-shield-loading");
    if (!el) return;
    el.classList.remove("is-visible");
    setTimeout(() => el.remove(), 150);
  }

  function showToast(message) {
    document.getElementById("klai-shield-toast")?.remove();
    const el = document.createElement("div");
    el.id = "klai-shield-toast";
    el.innerHTML = `${markSvg(14)}<span>${esc(message)}</span>`;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add("is-visible"));
    setTimeout(() => {
      el.classList.remove("is-visible");
      setTimeout(() => el.remove(), 220);
    }, 2600);
  }

  function showReview({ originalPrompt, enrichedPrompt, enrichments, check, onSend, onEdit, onCancel }) {
    document.getElementById("klai-review-overlay")?.remove();
    const overlay = document.createElement("div");
    overlay.id = "klai-review-overlay";
    const hasWarnings = (check.warnings || []).length > 0;
    const isBlocked = check.status === "red";
    const enrichmentHTML = enrichments.length
      ? enrichments.map((item) => `<div class="klai-r-tag">${esc(item.type === "rag" ? `Kennis: ${item.label}` : item.label)}</div>`).join("")
      : '<div class="klai-r-none">Geen kenniscontext actief</div>';
    const warningsHTML = hasWarnings
      ? check.warnings
          .slice(0, 5)
          .map((warning) => `
            <div class="klai-r-warning klai-r-warning-${esc(normalizeSeverity(warning.severity))}">
              <div class="klai-r-warning-top">
                <strong>${esc(warning.label || warning.id || "Waarschuwing")}</strong>
                ${warning.article ? `<span>${esc(warning.article)}</span>` : ""}
              </div>
              <div class="klai-r-warning-desc">${esc(warning.description || warning.message || "Controleer deze prompt voordat je verdergaat.")}</div>
            </div>
          `)
          .join("")
      : '<div class="klai-r-ok">Geen problemen gevonden</div>';
    const sendLabel = hasWarnings ? "Toch versturen" : "Versturen";
    const lightClass = `klai-r-light ${check.status || "green"}`;

    overlay.innerHTML = `
      <div class="klai-r-backdrop" id="klai-r-backdrop"></div>
      <div class="klai-r-modal" role="dialog" aria-modal="true" aria-label="Klai Shield pre-flight">
        <div class="klai-r-header">
          <div class="klai-r-title-row"><span class="${lightClass}"></span><span>Klai Shield pre-flight</span></div>
          <button class="klai-r-close" id="klai-r-close" type="button" aria-label="Sluiten">x</button>
        </div>
        <div class="klai-r-body">
          <section class="klai-r-section"><div class="klai-r-section-label">Jouw prompt</div><div class="klai-r-prompt">${esc(originalPrompt)}</div></section>
          <section class="klai-r-section"><div class="klai-r-section-label">Verrijking</div><div class="klai-r-tags">${enrichmentHTML}</div></section>
          <section class="klai-r-section"><div class="klai-r-section-label">Compliance</div>${warningsHTML}</section>
          ${enrichments.length ? `<details class="klai-r-details"><summary>Volledige prompt tonen</summary><pre>${esc(enrichedPrompt)}</pre></details>` : ""}
        </div>
        <div class="klai-r-footer">
          <button class="klai-r-btn klai-r-btn-secondary" id="klai-r-cancel" type="button">Annuleren</button>
          <button class="klai-r-btn klai-r-btn-secondary" id="klai-r-edit" type="button">Bewerken</button>
          ${isBlocked ? '<div class="klai-r-blocked">Geblokkeerd</div>' : `<button class="klai-r-btn klai-r-btn-primary" id="klai-r-send" type="button">${sendLabel}</button>`}
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add("is-visible"));
    const close = () => {
      overlay.classList.remove("is-visible");
      setTimeout(() => overlay.remove(), 180);
    };
    const cancel = () => {
      close();
      onCancel();
    };
    document.getElementById("klai-r-close").onclick = cancel;
    document.getElementById("klai-r-backdrop").onclick = cancel;
    document.getElementById("klai-r-cancel").onclick = cancel;
    document.getElementById("klai-r-edit").onclick = () => {
      close();
      onEdit();
    };
    const sendBtn = document.getElementById("klai-r-send");
    if (sendBtn) {
      sendBtn.onclick = () => {
        close();
        onSend(enrichedPrompt);
      };
    }
  }

  let isProcessing = false;
  let lastPromptWasEnriched = false;
  const site = detectSite();

  function interceptSubmit() {
    function handleSubmitAttempt(event) {
      if (event._klaiApproved || isProcessing || !isContextValid()) return;
      const textarea = getTextarea();
      const raw = getText(textarea).trim();
      if (!raw) return;
      event.stopImmediatePropagation();
      event.preventDefault();
      isProcessing = true;
      processPrompt(raw, textarea).finally(() => {
        isProcessing = false;
      });
    }

    async function processPrompt(raw, textarea) {
      try {
        const settings = await getSettings();
        if (settings.enabled === false) {
          sendToLLM(raw, textarea);
          return;
        }

        showLoading("Klai verrijkt en controleert...");
        const [enrichResult, check] = await Promise.all([
          enrichPrompt(raw, settings),
          checkAiAct(raw, settings)
        ]);
        hideLoading();

        const { enrichedPrompt, enrichments } = enrichResult;
        const logData = {
          check_type: "input",
          level: settings.complianceLevel || "basic",
          status: check.status,
          platform: site?.key || "",
          text_preview: raw.substring(0, 240),
          warnings: check.warnings || [],
          was_blocked: check.status === "red",
          was_overridden: false
        };

        if (check.status === "green") {
          runtimeMessage("KLAI_SHIELD_LOG_CHECK", { data: logData }).catch(() => {});
          lastPromptWasEnriched = enrichments.length > 0;
          sendToLLM(enrichedPrompt, textarea);
          if (enrichments.length) showToast(`Klai: ${enrichments.map((item) => item.label).join(" + ")} toegevoegd`);
          return;
        }

        showReview({
          originalPrompt: raw,
          enrichedPrompt,
          enrichments,
          check,
          onSend: (finalPrompt) => {
            logData.was_overridden = true;
            runtimeMessage("KLAI_SHIELD_LOG_CHECK", { data: logData }).catch(() => {});
            lastPromptWasEnriched = enrichments.length > 0;
            sendToLLM(finalPrompt, textarea);
          },
          onEdit: () => textarea?.focus(),
          onCancel: () => {
            runtimeMessage("KLAI_SHIELD_LOG_CHECK", { data: logData }).catch(() => {});
          }
        });
      } catch (error) {
        hideLoading();
        console.warn("[Klai Shield] Pre-flight failed, sending original prompt:", error.message);
        sendToLLM(raw, textarea);
      }
    }

    function sendToLLM(finalPrompt, textarea) {
      setText(textarea, finalPrompt);
      function trySubmit(attempt) {
        const btn = getSubmitButton();
        if (btn && !btn.disabled) {
          const click = new MouseEvent("click", { bubbles: true, cancelable: true });
          click._klaiApproved = true;
          btn.dispatchEvent(click);
          return;
        }
        if (attempt < 15) {
          setTimeout(() => trySubmit(attempt + 1), 150);
          return;
        }
        const enter = new KeyboardEvent("keydown", {
          key: "Enter",
          code: "Enter",
          keyCode: 13,
          which: 13,
          bubbles: true,
          cancelable: true
        });
        enter._klaiApproved = true;
        textarea?.dispatchEvent(enter);
      }
      setTimeout(() => trySubmit(0), 300);
    }

    function attachToButton(btn) {
      if (!btn || btn._klaiShieldAttached) return;
      btn._klaiShieldAttached = true;
      btn.addEventListener("click", handleSubmitAttempt, true);
    }

    const observer = new MutationObserver(() => {
      const btn = getSubmitButton();
      if (btn) attachToButton(btn);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    attachToButton(getSubmitButton());

    document.addEventListener(
      "keydown",
      (event) => {
        if (event.key !== "Enter" || event.shiftKey || event._klaiApproved || isProcessing || !isContextValid()) return;
        const textarea = getTextarea();
        if (!textarea) return;
        const active = document.activeElement;
        if (active !== textarea && !textarea.contains(active) && active?.closest?.("[contenteditable]") !== textarea) return;
        const raw = getText(textarea).trim();
        if (!raw) return;
        event.stopImmediatePropagation();
        event.preventDefault();
        isProcessing = true;
        processPrompt(raw, textarea).finally(() => {
          isProcessing = false;
        });
      },
      true
    );
  }

  let badgeData = { enabled: true, collections: 0, compliance: null };

  function markSvg(size = 14) {
    return `<svg class="klai-mark" width="${size}" height="${size}" viewBox="0 0 106 106" aria-hidden="true"><path d="${KLAI_MARK_PATH}" fill="currentColor"/></svg>`;
  }

  function removeTalkWithDataChrome() {
    document.body.classList.add("klai-shield-loaded");
    document.getElementById("superdock-shield-badge")?.remove();
    document.getElementById("superdock-shield-card")?.remove();
  }

  function createBadge() {
    removeTalkWithDataChrome();
    document.getElementById("klai-shield-badge")?.remove();
    document.getElementById("klai-shield-card")?.remove();
    const badge = document.createElement("button");
    badge.id = "klai-shield-badge";
    badge.type = "button";
    document.body.appendChild(badge);
    const card = document.createElement("div");
    card.id = "klai-shield-card";
    document.body.appendChild(card);
    updateBadge();

    badge.addEventListener("click", (event) => {
      event.stopPropagation();
      const panel = document.getElementById("klai-shield-card");
      panel.classList.toggle("is-open");
      if (panel.classList.contains("is-open")) renderCard();
    });
    document.addEventListener("click", () => {
      document.getElementById("klai-shield-card")?.classList.remove("is-open");
    });
  }

  async function updateBadge() {
    const badge = document.getElementById("klai-shield-badge");
    if (!badge) return;
    try {
      const settings = await getSettings();
      badgeData = {
        enabled: settings.enabled !== false,
        collections: (settings.activeKnowledgeBases || []).length,
        compliance: settings.complianceEnabled !== false ? normalizeLevel(settings.complianceLevel || "basic") : null
      };
    } catch (_) {
      badgeData = { enabled: false, collections: 0, compliance: null };
    }
    badge.className = badgeData.enabled ? "klai-badge-on" : "klai-badge-off";
    const label = badgeData.enabled
      ? badgeData.collections > 0
        ? `${badgeData.collections} bron${badgeData.collections === 1 ? "" : "nen"} actief`
        : "Klai aan"
      : "Klai uit";
    badge.innerHTML = `${markSvg(14)}<span>${esc(label)}</span>`;
  }

  function renderCard() {
    const card = document.getElementById("klai-shield-card");
    if (!card) return;
    const rows = [];
    rows.push(`<div class="klai-card-row"><span class="klai-card-dot ${badgeData.enabled ? "on" : "off"}"></span><strong>${badgeData.enabled ? "Klai Shield actief" : "Klai Shield uitgeschakeld"}</strong></div>`);
    if (badgeData.enabled) {
      if (badgeData.compliance) rows.push(`<div class="klai-card-row klai-card-detail">Compliance: ${esc(badgeData.compliance)}</div>`);
      if (badgeData.collections > 0) rows.push(`<div class="klai-card-row klai-card-detail">Kennis: ${badgeData.collections} bron${badgeData.collections === 1 ? "" : "nen"}</div>`);
      if (!badgeData.collections) rows.push('<div class="klai-card-row klai-card-detail klai-card-muted">Geen kennisbanken actief</div>');
    }
    rows.push('<div class="klai-card-hint">Open Klai Shield via het extensie-icoon in je toolbar</div>');
    card.innerHTML = rows.join("");
  }

  let mentionPopover = null;
  let mentionSourceEl = null;
  let mentionTriggerStart = -1;
  let mentionTriggerEnd = -1;

  function findKlaiTrigger(text, caretPos) {
    const before = String(text || "").slice(0, caretPos);
    const re = /@klai\b/gi;
    let last = null;
    let match;
    while ((match = re.exec(before)) !== null) last = match;
    if (!last) return null;
    const afterTrigger = before.slice(last.index + last[0].length);
    if (afterTrigger.includes("\n\n")) return null;
    return {
      start: last.index,
      end: caretPos,
      query: afterTrigger.trim()
    };
  }

  function showMentionPopover(el, trigger) {
    closeMentionPopover();
    const rect = el.getBoundingClientRect();
    const pop = document.createElement("div");
    pop.className = "klai-mp";
    pop.innerHTML = `
      <div class="klai-mp-head">
        <span class="klai-mp-logo">${markSvg(14)}</span>
        <span class="klai-mp-title">Klai</span>
        <span class="klai-mp-context">kenniscontext</span>
        <button class="klai-mp-close" type="button" aria-label="Sluiten">x</button>
      </div>
      <textarea class="klai-mp-input" rows="2" placeholder="Vraag aan je Klai kennis..."></textarea>
      <div class="klai-mp-foot">
        <span class="klai-mp-status"></span>
        <button class="klai-mp-send" type="button">Invoegen</button>
      </div>
    `;
    pop.style.position = "fixed";
    const width = 320;
    const height = 158;
    let left = rect.left;
    let top = rect.bottom + 6;
    if (left + width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - width - 8);
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 6);
    pop.style.top = `${top}px`;
    pop.style.left = `${left}px`;
    document.body.appendChild(pop);

    mentionPopover = pop;
    mentionSourceEl = el;
    mentionTriggerStart = trigger.start;
    mentionTriggerEnd = trigger.end;

    const input = pop.querySelector(".klai-mp-input");
    const sendBtn = pop.querySelector(".klai-mp-send");
    const closeBtn = pop.querySelector(".klai-mp-close");
    const status = pop.querySelector(".klai-mp-status");
    input.value = trigger.query || "";
    setTimeout(() => {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }, 0);

    const submit = async () => {
      const query = input.value.trim();
      if (!query) {
        input.focus();
        return;
      }
      sendBtn.disabled = true;
      status.textContent = "Klai zoekt...";
      try {
        const result = await queryKnowledge(query, 6);
        const context = formatChunksForInsert(result.chunks || [], { compact: true });
        if (!context) {
          status.textContent = "Geen context gevonden.";
          sendBtn.disabled = false;
          return;
        }
        replaceTriggerWithText(context);
        closeMentionPopover();
        showToast("Klai context ingevoegd");
      } catch (error) {
        status.textContent = (error.message || "Zoeken mislukt").slice(0, 90);
        sendBtn.disabled = false;
      }
    };

    sendBtn.addEventListener("click", submit);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submit();
      } else if (event.key === "Escape") {
        event.preventDefault();
        closeMentionPopover();
      }
    });
    closeBtn.addEventListener("click", closeMentionPopover);
  }

  function closeMentionPopover() {
    mentionPopover?.remove();
    mentionPopover = null;
    mentionSourceEl = null;
    mentionTriggerStart = -1;
    mentionTriggerEnd = -1;
  }

  function replaceTriggerWithText(insertText) {
    const el = mentionSourceEl || getTextarea();
    if (!el) return;
    const text = getText(el);
    const start = mentionTriggerStart >= 0 ? mentionTriggerStart : text.length;
    const end = mentionTriggerEnd >= start ? mentionTriggerEnd : text.length;
    const before = text.slice(0, start).replace(/[ \t]*$/, "");
    const after = text.slice(end).replace(/^[ \t]*/, "");
    const spacerBefore = before && !before.endsWith("\n") ? "\n\n" : "";
    const spacerAfter = after && !after.startsWith("\n") ? "\n\n" : "";
    const next = `${before}${spacerBefore}${insertText}${spacerAfter}${after}`;
    setText(el, next);
    setCaret(el, before.length + spacerBefore.length + insertText.length);
  }

  function insertContextIntoComposer(text) {
    const el = getTextarea() || document.activeElement;
    if (!isEditable(el)) {
      showToast("Open eerst een promptveld");
      return false;
    }
    const current = getText(el);
    const caret = getCaretPos(el);
    const trigger = findKlaiTrigger(current, caret);
    if (trigger) {
      mentionSourceEl = el;
      mentionTriggerStart = trigger.start;
      mentionTriggerEnd = trigger.end;
      replaceTriggerWithText(text);
    } else {
      const separator = current.trim() ? "\n\n" : "";
      setText(el, `${current.trimEnd()}${separator}${text}`);
      setCaret(el, `${current.trimEnd()}${separator}${text}`.length);
    }
    showToast("Klai context ingevoegd");
    return true;
  }

  function setupKlaiMention() {
    document.addEventListener(
      "input",
      (event) => {
        const el = event.target;
        if (!isEditable(el)) return;
        if (mentionPopover?.contains(el)) return;
        const text = getText(el);
        const caret = getCaretPos(el);
        const trigger = findKlaiTrigger(text, caret);
        if (trigger) {
          if (mentionSourceEl === el && mentionTriggerStart === trigger.start && mentionTriggerEnd === trigger.end) return;
          showMentionPopover(el, trigger);
        } else if (mentionSourceEl === el) {
          closeMentionPopover();
        }
      },
      true
    );
    document.addEventListener(
      "mousedown",
      (event) => {
        if (!mentionPopover) return;
        if (mentionPopover.contains(event.target)) return;
        if (event.target === mentionSourceEl) return;
        closeMentionPopover();
      },
      true
    );
  }

  try {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message.type === "KLAI_SHIELD_INSERT_CONTEXT") {
        const ok = insertContextIntoComposer(message.text || "");
        sendResponse({ success: ok, ok });
        return true;
      }
      if (message.type === "KLAI_SHIELD_SETTINGS_CHANGED" || message.type === "KLAI_SHIELD_TOGGLE") {
        updateBadge();
        sendResponse({ success: true, ok: true });
        return true;
      }
      return false;
    });
  } catch (_) {}

  function init() {
    setupKlaiMention();
    if (!site) return;
    createBadge();
    interceptSubmit();
    setInterval(updateBadge, 8000);
  }

  init();
})();
