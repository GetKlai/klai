/**
 * Widget UI labels in NL and EN.
 * Selected by browser locale or widget_config override.
 */

export interface WidgetLabels {
  placeholder: string
  sendMessage: string
  stopGenerating: string
  closeChat: string
  openChat: string
  inputLabel: string
  messagesLabel: string
  errorGeneric: string
  errorSessionExpired: string
}

const nl: WidgetLabels = {
  placeholder: "Stel een vraag...",
  sendMessage: "Stuur bericht",
  stopGenerating: "Stop genereren",
  closeChat: "Sluit chat",
  openChat: "Open chat",
  inputLabel: "Berichtinvoer",
  messagesLabel: "Chatberichten",
  errorGeneric: "Er ging iets mis. Probeer het opnieuw.",
  errorSessionExpired: "Sessie verlopen. Herlaad de pagina.",
}

const en: WidgetLabels = {
  placeholder: "Ask a question...",
  sendMessage: "Send message",
  stopGenerating: "Stop generating",
  closeChat: "Close chat",
  openChat: "Open chat",
  inputLabel: "Message input",
  messagesLabel: "Chat messages",
  errorGeneric: "Something went wrong. Please try again.",
  errorSessionExpired: "Session expired. Reload the page.",
}

const locales: Record<string, WidgetLabels> = { nl, en }

let _labels: WidgetLabels = nl

export function initLabels(locale?: string): void {
  const lang = locale || navigator.language?.slice(0, 2) || "nl"
  _labels = locales[lang] ?? locales.en ?? nl
}

export function t(): WidgetLabels {
  return _labels
}
