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
  disclaimer: string
  handoffButton: string
  handoffConnecting: string
  handoffConnected: string
  userInfoName: string
  userInfoEmail: string
  userInfoHelp: string
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
  disclaimer: "AI-antwoorden kunnen fouten bevatten. Verifieer belangrijke informatie altijd bij de bron.",
  handoffButton: "Praat met een medewerker",
  handoffConnecting: "Ik verbind je met een medewerker.",
  handoffConnected: "Je bent verbonden met een medewerker.",
  userInfoName: "Naam",
  userInfoEmail: "E-mail",
  userInfoHelp: "Laat je gegevens achter voor opvolging.",
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
  disclaimer: "AI answers can contain mistakes. Always verify important information at the source.",
  handoffButton: "Talk to a human",
  handoffConnecting: "I am connecting you with a human agent.",
  handoffConnected: "You are connected with a human agent.",
  userInfoName: "Name",
  userInfoEmail: "Email",
  userInfoHelp: "Leave your details for follow-up.",
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
