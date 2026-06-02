const els = {
  loginView: document.querySelector("#login-view"),
  appView: document.querySelector("#app-view"),
  login: document.querySelector("#login-btn"),
  loginError: document.querySelector("#login-error"),
  avatar: document.querySelector("#avatar"),
  userName: document.querySelector("#user-name"),
  userEmail: document.querySelector("#user-email"),
  shieldState: document.querySelector("#shield-state"),
  kbCount: document.querySelector("#kb-count"),
  openSidepanel: document.querySelector("#open-sidepanel"),
  refresh: document.querySelector("#refresh-btn"),
  logout: document.querySelector("#logout-btn")
};

async function msg(type, payload = {}) {
  const response = await chrome.runtime.sendMessage({ type, ...payload });
  if (!response?.ok && !response?.success) {
    throw new Error(response?.error || "Klai Shield request failed.");
  }
  return response.result || response;
}

async function init() {
  try {
    const auth = await msg("KLAI_SHIELD_GET_AUTH");
    if (auth?.authenticated) {
      await showApp();
    } else {
      showLogin();
    }
  } catch (error) {
    showLogin(error.message || "");
  }
}

function showLogin(error = "") {
  els.loginView.classList.remove("hidden");
  els.appView.classList.add("hidden");
  els.loginError.textContent = error;
}

async function showApp() {
  const settings = await msg("KLAI_SHIELD_GET_SETTINGS");
  const user = settings.user || settings.config?.user || {};
  const kbs = settings.knowledgeBases || settings.config?.knowledge_bases || [];
  els.loginView.classList.add("hidden");
  els.appView.classList.remove("hidden");
  els.userName.textContent = user.display_name || user.name || user.email || "Klai gebruiker";
  els.userEmail.textContent = user.email || "";
  els.avatar.textContent = (els.userName.textContent || "K").slice(0, 1).toUpperCase();
  els.shieldState.textContent = settings.enabled === false ? "Uit" : "Actief";
  els.kbCount.textContent = String(kbs.length);
}

els.login.addEventListener("click", async () => {
  els.login.disabled = true;
  els.loginError.textContent = "Klai login openen...";
  try {
    await msg("KLAI_SHIELD_LOGIN");
    await showApp();
  } catch (error) {
    els.loginError.textContent = error.message || "Inloggen mislukt.";
  } finally {
    els.login.disabled = false;
  }
});

els.openSidepanel.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.windowId && chrome.sidePanel?.open) {
    await chrome.sidePanel.open({ windowId: tab.windowId });
    window.close();
  }
});

els.refresh.addEventListener("click", async () => {
  els.refresh.disabled = true;
  try {
    await msg("KLAI_SHIELD_REFRESH_DATA");
    await showApp();
  } finally {
    els.refresh.disabled = false;
  }
});

els.logout.addEventListener("click", async () => {
  await msg("KLAI_SHIELD_LOGOUT");
  showLogin();
});

init();
