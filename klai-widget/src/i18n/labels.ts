/**
 * Widget UI labels in NL and EN.
 * Selected by explicit widget locale, then widget copy, then page/browser locale.
 */

import { createSignal } from "solid-js"

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
  // Nerds booking panel (Voys-specific). While the integration is on, the
  // three disclosure fragments REPLACE the plain accuracy footer below the
  // input; the NL wording is client-supplied verbatim — do not rephrase.
  // The link fragment renders as a <button> that opens the iframe panel.
  nerdsDisclosureBefore: string
  nerdsDisclosureLink: string
  nerdsDisclosureAfter: string
  nerdsPanelTitle: string
  nerdsPanelClose: string
  nerdsPanelFallback: string
  handoffButton: string
  handoffConnecting: string
  handoffConnected: string
  userInfoName: string
  userInfoEmail: string
  userInfoHelp: string
  userInfoTitle: string
  userInfoStart: string
  userInfoSkip: string
  handoffConnectedWith: string
  handoffNamePlaceholder: string
  rememberMe: string
  clearStoredIdentity: string
  conversationHistory: string
  newConversation: string
  onlineStatus: string
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
  // Sources/agent-activity disclosure under an assistant answer, plus the
  // meta line under it. The count strings carry a {count} placeholder; the
  // one/other split covers 1 vs everything else (0 included) in both
  // languages.
  sourcesTitle: string
  sourceCountOne: string
  sourceCountOther: string
  agentActivityTitle: string
  activityCountOne: string
  activityCountOther: string
  answerBasedOnSourcesOne: string
  answerBasedOnSourcesOther: string
}

const nl: WidgetLabels = {
  placeholder: "Beschrijf je vraag zo concreet mogelijk...",
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
  // Client-supplied verbatim — the sentence reads "… afspraak in met " +
  // "onze nerds" + "."; the trailing/leading spaces carry the spacing.
  nerdsDisclosureBefore:
    "De Voys AI chat baseert zich op zorgvuldig gekozen bronnen. Toch kunnen ook daar fouten in staan, dus vertrouw ze niet blind. Kom je er niet uit in de chat? Plan dan een afspraak in met ",
  nerdsDisclosureLink: "onze nerds",
  nerdsDisclosureAfter: ".",
  nerdsPanelTitle: "Afspraak inplannen",
  nerdsPanelClose: "Terug naar de chat",
  nerdsPanelFallback: "Openen in een nieuw tabblad",
  handoffButton: "Praat met een medewerker",
  handoffConnecting: "Ik verbind je met een medewerker.",
  handoffConnected: "Je bent verbonden met een medewerker.",
  userInfoName: "Naam",
  userInfoEmail: "E-mailadres",
  userInfoHelp:
    "We lezen elk gesprek na om te zien hoe goed de AI antwoordt. Klopt een antwoord niet, dan sturen we je per e-mail een correctie.",
  userInfoTitle: "Voordat we beginnen",
  userInfoStart: "Begin het gesprek",
  userInfoSkip: "Liever niet, ga direct naar de chat",
  handoffConnectedWith: "Je praat met {name}.",
  handoffNamePlaceholder: "Je naam",
  rememberMe: "Onthoud mijn gegevens (30 dagen)",
  clearStoredIdentity: "Wis opgeslagen gegevens",
  conversationHistory: "Gesprekken",
  newConversation: "Nieuw gesprek",
  onlineStatus: "Online",
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
  sourcesTitle: "Bronnen",
  sourceCountOne: "1 bron",
  sourceCountOther: "{count} bronnen",
  agentActivityTitle: "Agent activiteit",
  activityCountOne: "1 stap",
  activityCountOther: "{count} stappen",
  answerBasedOnSourcesOne: "Antwoord gebaseerd op 1 bron uit de kennisbank.",
  answerBasedOnSourcesOther: "Antwoord gebaseerd op {count} bronnen uit de kennisbank.",
}

const en: WidgetLabels = {
  placeholder: "Describe your question as specifically as you can...",
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
  nerdsDisclosureBefore:
    "The Voys AI chat draws on carefully chosen sources. Even those can contain mistakes, so don't trust them blindly. Can't work it out in the chat? Then schedule an appointment with ",
  nerdsDisclosureLink: "our nerds",
  nerdsDisclosureAfter: ".",
  nerdsPanelTitle: "Schedule an appointment",
  nerdsPanelClose: "Back to the chat",
  nerdsPanelFallback: "Open in a new tab",
  handoffButton: "Talk to a human",
  handoffConnecting: "I am connecting you with a human agent.",
  handoffConnected: "You are connected with a human agent.",
  userInfoName: "Name",
  userInfoEmail: "Email address",
  userInfoHelp:
    "We read every conversation to see how well the AI answered. If an answer turns out to be wrong, we email you a correction.",
  userInfoTitle: "Before we start",
  userInfoStart: "Start the chat",
  userInfoSkip: "No thanks, take me to the chat",
  handoffConnectedWith: "You are talking to {name}.",
  handoffNamePlaceholder: "Your name",
  rememberMe: "Remember my details (30 days)",
  clearStoredIdentity: "Clear stored details",
  conversationHistory: "Conversations",
  newConversation: "New conversation",
  onlineStatus: "Online",
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
  sourcesTitle: "Sources",
  sourceCountOne: "1 source",
  sourceCountOther: "{count} sources",
  agentActivityTitle: "Agent activity",
  activityCountOne: "1 step",
  activityCountOther: "{count} steps",
  answerBasedOnSourcesOne: "Answer based on 1 source from the knowledge base.",
  answerBasedOnSourcesOther: "Answer based on {count} sources from the knowledge base.",
}

const locales: Record<string, WidgetLabels> = { nl, en }

// A signal, not a plain module variable: t() is called from inside ~70 JSX
// expressions, and only a signal accessor makes Solid re-render them when
// the active label set changes mid-conversation (see setLanguage below).
const [labelsSignal, setLabelsSignal] = createSignal<WidgetLabels>(nl)

// Frozen at load time on purpose, unlike the label set above. The Nerds
// booking panel is a third-party page loaded in an iframe; its language is
// picked once from this value and stays out of the per-turn switching.
let _initialLocale: "nl" | "en" = "nl"

export function initLabels(locale?: string, samples: string[] = []): void {
  const explicitLang = locale?.slice(0, 2).toLowerCase()
  const lang =
    explicitLang ||
    detectLanguageFromSamples(samples) ||
    document.documentElement.lang?.slice(0, 2).toLowerCase() ||
    navigator.language?.slice(0, 2).toLowerCase() ||
    "nl"
  const labels = locales[lang] ?? locales.en ?? nl
  setLabelsSignal(labels)
  _initialLocale = labels === en ? "en" : "nl"
}

/** Switches the active label set mid-conversation, driven by the backend's
 * per-turn language signal (chat-stream.ts normalizeLanguage). Only "nl"
 * and "en" have a label set, so the caller already filters out anything
 * else — this never falls back to a default, it only ever moves to a label
 * set the widget actually has. */
export function setLanguage(code: "nl" | "en"): void {
  setLabelsSignal(locales[code])
}

// The locale the labels were resolved to at load time. The Nerds booking
// panel sends it as the embed's `lng` parameter; that framed page is loaded
// externally and deliberately does NOT follow the conversation language, so
// this must not read the label signal.
export function currentLocale(): "nl" | "en" {
  return _initialLocale
}

export function t(): WidgetLabels {
  return labelsSignal()
}

function pluralLabel(count: number, one: string, other: string): string {
  return count === 1 ? one : other.replace("{count}", String(count))
}

export function sourceCountLabel(count: number): string {
  return pluralLabel(count, t().sourceCountOne, t().sourceCountOther)
}

export function activityCountLabel(count: number): string {
  return pluralLabel(count, t().activityCountOne, t().activityCountOther)
}

export function answerBasedOnSourcesLabel(count: number): string {
  return pluralLabel(count, t().answerBasedOnSourcesOne, t().answerBasedOnSourcesOther)
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
