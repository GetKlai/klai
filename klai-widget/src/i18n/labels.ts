/**
 * Widget UI labels in NL and EN.
 * Selected by explicit widget locale, then widget copy, then page/browser locale.
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
  // Accuracy footer; hideable per widget via hide_disclaimer (white-label).
  disclaimer: string
  // EU AI Act art. 50 notice — NOT hideable, see ChatWindow hero.
  // aiDisclosure carries the {name} placeholder (per-tenant bot name from
  // the widget config); aiDisclosureNoOrg is the same notice for widgets
  // without a name, so the sentence never renders a hole.
  //
  // The appointment sentence is SEPARATE and conditional on a configured
  // booking_url. The AI notice itself is a legal requirement and shows
  // unconditionally; promising a person to a visitor whose widget has no
  // booking route would be a promise with no button behind it.
  aiDisclosure: string
  aiDisclosureNoOrg: string
  aiDisclosureBooking: string
  bookingButton: string
  handoffButton: string
  handoffConnecting: string
  handoffConnected: string
  userInfoName: string
  userInfoEmail: string
  userInfoHelp: string
  handoffConnectedWith: string
  handoffNamePlaceholder: string
  rememberMe: string
  clearStoredIdentity: string
  conversationHistory: string
  newConversation: string
  closeConversation: string
  feedbackGroupLabel: string
  feedbackHelpful: string
  feedbackNotHelpful: string
  conversationClosed: string
  conversationActive: string
  conversationHandoff: string
  noPreviousConversations: string
  // Helpdesk broad mode: shown only after the backend marks a refusal with
  // the offer signal (help articles came up empty). The button's click sends
  // broadConsentMessage as the visitor's turn, so it reads as natural
  // first-person speech.
  broadOfferPrompt: string
  broadOfferButton: string
  broadConsentMessage: string
  // Indicator bar, shown while consent is on or after any broad answer in
  // this conversation. Turning mode off/on from there is equally explicit;
  // the label states what the mode actually does.
  broadModeOnLabel: string
  broadModeOffButton: string
  broadModePausedLabel: string
  broadModeOnButton: string
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
  aiDisclosure:
    "Je praat met een AI-assistent die veel weet over {name} en waar mogelijk de bronnen bij zijn antwoord zet.",
  aiDisclosureNoOrg:
    "Je praat met een AI-assistent die waar mogelijk de bronnen bij zijn antwoord zet.",
  aiDisclosureBooking:
    " Kom je er samen niet uit, dan kun je een afspraak inplannen met een medewerker die je persoonlijk verderhelpt.",
  bookingButton: "Plan een afspraak",
  handoffButton: "Praat met een medewerker",
  handoffConnecting: "Ik verbind je met een medewerker.",
  handoffConnected: "Je bent verbonden met een medewerker.",
  userInfoName: "Naam",
  userInfoEmail: "E-mail",
  userInfoHelp: "Laat je gegevens achter voor opvolging.",
  handoffConnectedWith: "Je praat met {name}.",
  handoffNamePlaceholder: "Je naam",
  rememberMe: "Onthoud mijn gegevens (30 dagen)",
  clearStoredIdentity: "Wis opgeslagen gegevens",
  conversationHistory: "Gesprekken",
  newConversation: "Nieuw gesprek",
  closeConversation: "Sluit gesprek",
  feedbackGroupLabel: "Beoordeel dit antwoord",
  feedbackHelpful: "Nuttig",
  feedbackNotHelpful: "Niet nuttig",
  conversationClosed: "Gesprek gesloten",
  conversationActive: "Actief",
  conversationHandoff: "Live support",
  noPreviousConversations: "Nog geen eerdere gesprekken.",
  broadOfferPrompt:
    "Ik kon dit niet in de helpartikelen vinden. Zal ik het breder opzoeken? Je krijgt dan een algemeen antwoord, duidelijk gelabeld als niet-afkomstig uit onze artikelen.",
  broadOfferButton: "Ja, kijk breder",
  broadConsentMessage: "Ja, kijk breder.",
  broadModeOnLabel: "Brede modus aan — antwoorden buiten de helpartikelen zijn gelabeld.",
  broadModeOffButton: "Zet uit",
  broadModePausedLabel: "Brede modus uit — de bot antwoordt weer alleen uit de helpartikelen.",
  broadModeOnButton: "Zet aan",
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
  aiDisclosure:
    "You are chatting with an AI assistant that knows a lot about {name} and adds sources to its answers where it can.",
  aiDisclosureNoOrg:
    "You are chatting with an AI assistant that adds sources to its answers where it can.",
  aiDisclosureBooking:
    " If you don't work it out together, you can schedule an appointment with an employee who will help you personally.",
  bookingButton: "Schedule an appointment",
  handoffButton: "Talk to a human",
  handoffConnecting: "I am connecting you with a human agent.",
  handoffConnected: "You are connected with a human agent.",
  userInfoName: "Name",
  userInfoEmail: "Email",
  userInfoHelp: "Leave your details for follow-up.",
  handoffConnectedWith: "You are talking to {name}.",
  handoffNamePlaceholder: "Your name",
  rememberMe: "Remember my details (30 days)",
  clearStoredIdentity: "Clear stored details",
  conversationHistory: "Conversations",
  newConversation: "New conversation",
  closeConversation: "Close conversation",
  feedbackGroupLabel: "Rate this answer",
  feedbackHelpful: "Helpful",
  feedbackNotHelpful: "Not helpful",
  conversationClosed: "Conversation closed",
  conversationActive: "Active",
  conversationHandoff: "Live support",
  noPreviousConversations: "No previous conversations yet.",
  broadOfferPrompt:
    "I couldn't find this in our help articles. Want me to look wider? You'll get a general answer, clearly labelled as not coming from our articles.",
  broadOfferButton: "Yes, look broader",
  broadConsentMessage: "Yes, look broader.",
  broadModeOnLabel: "Broad mode on — answers outside the help articles are labelled.",
  broadModeOffButton: "Turn off",
  broadModePausedLabel: "Broad mode off — the bot again answers only from the help articles.",
  broadModeOnButton: "Turn on",
}

const locales: Record<string, WidgetLabels> = { nl, en }

let _labels: WidgetLabels = nl

export function initLabels(locale?: string, samples: string[] = []): void {
  const explicitLang = locale?.slice(0, 2).toLowerCase()
  const lang =
    explicitLang ||
    detectLanguageFromSamples(samples) ||
    document.documentElement.lang?.slice(0, 2).toLowerCase() ||
    navigator.language?.slice(0, 2).toLowerCase() ||
    "nl"
  _labels = locales[lang] ?? locales.en ?? nl
}

export function t(): WidgetLabels {
  return _labels
}

function detectLanguageFromSamples(samples: string[]): string | undefined {
  const text = samples.join(" ").toLowerCase()
  if (!text) return undefined
  const dutchMarkers = [
    " voor ",
    " vraag ",
    " vragen ",
    " gesprek",
    " gebruiker",
    " gebruikers",
    " toevoegen",
    " kennis",
    " waarmee",
    " stel ",
    " je ",
    " jij ",
  ]
  return dutchMarkers.some((marker) => text.includes(marker)) ? "nl" : undefined
}
