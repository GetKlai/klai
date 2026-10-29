"""Fixed Slack UI strings, Dutch first with an English variant (SPEC-SLACK-001 §9).

The language comes from ``slack_users.user_lang``. Answers themselves are in
whatever language the Partner API answers in; only Klai's own UI is translated.
"""

from __future__ import annotations

from typing import Any

NL: dict[str, str] = {
    "searched_in": "Gezocht in: {names}",
    "all_allowed": "alle vrijgegeven kennisbanken",
    "sources": "Bronnen",
    "pick_kbs": "Kennisbanken kiezen",
    "all_sources": "Alle {n} bronnen",
    "helpful": "Nuttig",
    "not_helpful": "Niet nuttig",
    "placeholder_one": "Ik zoek in 1 kennisbank…",
    "placeholder_many": "Ik zoek in {n} kennisbanken…",
    "not_linked": (
        "Klai is nog niet gekoppeld aan deze Slack-workspace. Een Klai-beheerder "
        "koppelt Klai vanuit de Klai-portal (Beheer → Slack)."
    ),
    "no_kbs": (
        "Er zijn nog geen kennisbanken vrijgegeven voor Slack. Vraag je Klai-beheerder "
        "om kennisbanken vrij te geven in de Klai-portal (Beheer > Slack)."
    ),
    "empty_scope": (
        "De gekozen kennisbanken voor dit gesprek zijn niet meer vrijgegeven, dus ik heb "
        "niet gezocht. Kies opnieuw met *Kennisbanken kiezen*, of vraag een "
        "workspace-beheerder om `/klai kb` in dit kanaal."
    ),
    "rate_limited": "Klai krijgt nu te veel vragen tegelijk. Probeer het over {seconds} s opnieuw.",
    "rate_limited_unknown": "Klai krijgt nu te veel vragen tegelijk. Probeer het zo opnieuw.",
    "retry": "Opnieuw proberen",
    "unavailable": (
        "Klai is nu niet bereikbaar. Probeer het over een paar minuten opnieuw. "
        "(request-id `{request_id}`)"
    ),
    "key_refused": (
        "De koppeling met Klai werkt niet meer. Vraag je Klai-beheerder om de "
        "Slack-koppeling in de Klai-portal te controleren. (request-id `{request_id}`)"
    ),
    "unexpected": "Er ging iets mis bij Klai. Probeer het opnieuw.",
    "empty_question": "Stel je vraag na @Klai, dan zoek ik het op.",
    "retry_expired": "Deze vraag is verlopen. Stel hem opnieuw.",
    "sources_title": "Bronnen",
    "sources_expired": (
        "De bronnen van dit antwoord zijn niet meer beschikbaar. Stel de vraag opnieuw."
    ),
    "close": "Sluiten",
    "kb_title": "Kennisbanken",
    "save": "Opslaan",
    "cancel": "Annuleren",
    "loading": "Kennisbanken laden…",
    "kb_label_channel": "Kennisbanken voor dit kanaal",
    "kb_label_thread": "Kennisbanken voor dit gesprek",
    "kb_hint_channel": "Leeg laten = alle vrijgegeven kennisbanken",
    "kb_hint_thread": "Leeg laten = de kennisbanken van dit kanaal",
    "kb_placeholder": "Kies kennisbanken",
    "kb_more": "Er zijn meer dan {n} kennisbanken vrijgegeven; alleen de eerste {n} staan hier.",
    "kb_admin_only": (
        "Alleen beheerders en eigenaren van deze Slack-workspace kunnen de kennisbanken "
        "van een kanaal instellen."
    ),
    "slack_error": (
        "Ik kon je Slack-rechten niet controleren. Probeer het over een paar minuten opnieuw."
    ),
    "saved_channel": "Klai zoekt in dit kanaal nu in: {names}.",
    "saved_thread": "Klai zoekt in dit gesprek nu in: {names}.",
    "invalid_selection": (
        "Een of meer gekozen kennisbanken zijn niet (meer) beschikbaar. Er is niets "
        "opgeslagen; open de keuze opnieuw."
    ),
    "feedback_thanks": "Bedankt voor je feedback.",
    "feedback_failed": "Je feedback kon niet worden verstuurd. Probeer het later opnieuw.",
    "feedback_title": "Feedback",
    "feedback_send": "Versturen",
    "feedback_reason": "Wat ging er mis?",
    "feedback_incorrect": "Het antwoord klopt niet",
    "feedback_incomplete": "Het antwoord is onvolledig",
    "feedback_irrelevant": "Het antwoord past niet bij mijn vraag",
    "feedback_sources": "De bronnen kloppen niet",
    "feedback_text": "Toelichting (optioneel)",
    "help": (
        "*Klai* beantwoordt vragen uit de kennisbanken die jullie organisatie voor Slack "
        "heeft vrijgegeven, en laat zien waar het antwoord vandaan komt.\n"
        "• Stel je vraag in een DM met Klai, of noem @Klai in een kanaal. Klai antwoordt "
        "altijd in de thread.\n"
        "• *Kennisbanken kiezen* onder een antwoord beperkt één gesprek.\n"
        "• `/klai kb` (workspace-beheerders) kiest de kennisbanken voor een kanaal.\n\n"
        "*In dit kanaal:* {scope}\n"
        "Meer staat in de <{home_url}|App Home van Klai>."
    ),
    "home_connected": "Verbonden met Klai-organisatie *{org}*.",
    "home_not_linked": "*Nog niet gekoppeld.*",
    "home_connect_hint": "Koppel Klai vanuit de <{portal_url}|Klai-portal> (Beheer → Slack).",
    "home_kbs": "*Vrijgegeven kennisbanken*",
    "home_kbs_manage": "<{portal_url}|Beheren in de Klai-portal>",
    "home_kbs_none": "Nog geen kennisbanken vrijgegeven.",
    "home_kbs_error": "De kennisbanken konden niet worden geladen. Probeer het later opnieuw.",
    "home_channels": "*Kanalen met een eigen keuze*",
    "home_channels_none": "Geen; overal zoekt Klai in alle vrijgegeven kennisbanken.",
    "home_channel_revoked": "geen vrijgegeven kennisbank meer, kies opnieuw met `/klai kb`",
    "home_help": (
        "*Zo gebruik je Klai*\n"
        "• Stel je vraag in het tabblad Berichten of noem @Klai in een kanaal.\n"
        "• `/klai kb` (beheerders) kiest de kennisbanken voor een kanaal.\n"
        "• `/klai help` laat zien waarin Klai in een kanaal zoekt."
    ),
    "home_disclaimer": (
        "Klai antwoordt alleen uit de vrijgegeven kennisbanken en kan zich vergissen. "
        "Controleer belangrijke antwoorden in de bron."
    ),
    "prompts_title": (
        "Ik zoek in alle kennisbanken die jullie organisatie voor Slack heeft vrijgegeven. "
        'Wil je in minder zoeken? Kies onder een antwoord "Kennisbanken kiezen".'
    ),
    "prompt_1_title": "Beleid opzoeken",
    "prompt_1_message": "Wat is ons beleid voor thuiswerken?",
    "prompt_2_title": "Proces volgen",
    "prompt_2_message": "Hoe onboarden we een nieuwe klant?",
    "prompt_3_title": "Prijs opzoeken",
    "prompt_3_message": "Wat zijn onze prijzen en pakketten?",
    "prompt_4_title": "Voorwaarden nakijken",
    "prompt_4_message": "Wat zijn onze voorwaarden voor het opzeggen van een contract?",
}

EN: dict[str, str] = {
    "searched_in": "Searched: {names}",
    "all_allowed": "all released knowledge bases",
    "sources": "Sources",
    "pick_kbs": "Choose knowledge bases",
    "all_sources": "All {n} sources",
    "helpful": "Helpful",
    "not_helpful": "Not helpful",
    "placeholder_one": "Searching 1 knowledge base…",
    "placeholder_many": "Searching {n} knowledge bases…",
    "not_linked": (
        "Klai is not linked to this Slack workspace yet. A Klai admin links Klai from "
        "the Klai portal (Admin → Slack)."
    ),
    "no_kbs": (
        "No knowledge bases have been released for Slack yet. Ask your Klai admin to "
        "release knowledge bases in the Klai portal (Admin > Slack)."
    ),
    "empty_scope": (
        "The knowledge bases chosen for this conversation are no longer released, so I "
        "did not search. Choose again with *Choose knowledge bases*, or ask a workspace "
        "admin to run `/klai kb` in this channel."
    ),
    "rate_limited": "Klai is getting too many questions at once. Try again in {seconds} s.",
    "rate_limited_unknown": "Klai is getting too many questions at once. Try again shortly.",
    "retry": "Try again",
    "unavailable": (
        "Klai cannot be reached right now. Try again in a few minutes. (request id `{request_id}`)"
    ),
    "key_refused": (
        "The link with Klai no longer works. Ask your Klai admin to check the Slack "
        "link in the Klai portal. (request id `{request_id}`)"
    ),
    "unexpected": "Something went wrong at Klai. Please try again.",
    "empty_question": "Ask your question after @Klai and I will look it up.",
    "retry_expired": "This question has expired. Please ask it again.",
    "sources_title": "Sources",
    "sources_expired": "The sources of this answer are no longer available. Ask again.",
    "close": "Close",
    "kb_title": "Knowledge bases",
    "save": "Save",
    "cancel": "Cancel",
    "loading": "Loading knowledge bases…",
    "kb_label_channel": "Knowledge bases for this channel",
    "kb_label_thread": "Knowledge bases for this conversation",
    "kb_hint_channel": "Leave empty = all released knowledge bases",
    "kb_hint_thread": "Leave empty = this channel's knowledge bases",
    "kb_placeholder": "Choose knowledge bases",
    "kb_more": "More than {n} knowledge bases are released; only the first {n} are listed.",
    "kb_admin_only": (
        "Only admins and owners of this Slack workspace can set a channel's knowledge bases."
    ),
    "slack_error": "I could not check your Slack permissions. Try again in a few minutes.",
    "saved_channel": "Klai now searches in this channel: {names}.",
    "saved_thread": "Klai now searches in this conversation: {names}.",
    "invalid_selection": (
        "One or more chosen knowledge bases are not (or no longer) available. Nothing was "
        "saved; open the picker again."
    ),
    "feedback_thanks": "Thanks for your feedback.",
    "feedback_failed": "Your feedback could not be sent. Please try again later.",
    "feedback_title": "Feedback",
    "feedback_send": "Send",
    "feedback_reason": "What went wrong?",
    "feedback_incorrect": "The answer is wrong",
    "feedback_incomplete": "The answer is incomplete",
    "feedback_irrelevant": "The answer does not fit my question",
    "feedback_sources": "The sources are wrong",
    "feedback_text": "Details (optional)",
    "help": (
        "*Klai* answers questions from the knowledge bases your organisation released "
        "for Slack, and shows where each answer comes from.\n"
        "• Ask in a DM with Klai, or mention @Klai in a channel. Klai always answers in "
        "the thread.\n"
        "• *Choose knowledge bases* below an answer narrows one conversation.\n"
        "• `/klai kb` (workspace admins) sets the knowledge bases for a channel.\n\n"
        "*In this channel:* {scope}\n"
        "More in <{home_url}|Klai's App Home>."
    ),
    "home_connected": "Connected to Klai organisation *{org}*.",
    "home_not_linked": "*Not linked yet.*",
    "home_connect_hint": "Link Klai from the <{portal_url}|Klai portal> (Admin → Slack).",
    "home_kbs": "*Released knowledge bases*",
    "home_kbs_manage": "<{portal_url}|Manage in the Klai portal>",
    "home_kbs_none": "No knowledge bases released yet.",
    "home_kbs_error": "The knowledge bases could not be loaded. Please try again later.",
    "home_channels": "*Channels with their own choice*",
    "home_channels_none": "None; everywhere Klai searches all released knowledge bases.",
    "home_channel_revoked": "no released knowledge base left, choose again with `/klai kb`",
    "home_help": (
        "*How to use Klai*\n"
        "• Ask in the Messages tab or mention @Klai in a channel.\n"
        "• `/klai kb` (admins) sets the knowledge bases for a channel.\n"
        "• `/klai help` shows what Klai searches in a channel."
    ),
    "home_disclaimer": (
        "Klai only answers from the released knowledge bases and can be wrong. Check "
        "important answers in the source."
    ),
    "prompts_title": (
        "I search every knowledge base your organisation has released for Slack. "
        'Want to search fewer? Use "Choose knowledge bases" under an answer.'
    ),
    "prompt_1_title": "Check a policy",
    "prompt_1_message": "What is our policy on working from home?",
    "prompt_2_title": "Follow a process",
    "prompt_2_message": "How do we onboard a new customer?",
    "prompt_3_title": "Look up a price",
    "prompt_3_message": "What are our prices and plans?",
    "prompt_4_title": "Check our terms",
    "prompt_4_message": "What are our terms for cancelling a contract?",
}

_STRINGS = {"nl": NL, "en": EN}


def t(lang: str, key: str, **kwargs: Any) -> str:
    text = _STRINGS.get(lang, NL)[key]
    return text.format(**kwargs) if kwargs else text


def placeholder(lang: str, n: int) -> str:
    return t(lang, "placeholder_one") if n == 1 else t(lang, "placeholder_many", n=n)
