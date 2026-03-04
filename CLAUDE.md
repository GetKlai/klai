# Klai: Gedeelde Claude-configuratie

Dit zijn de gedeelde basisinstructies voor alle Klai-projecten.
Projectspecifieke instructies staan in het CLAUDE.md van het betreffende project.

## Over Klai

Klai bouwt AI-aangedreven go-to-market tools voor B2B sales- en marketingteams.
Primaire taal: Nederlands. Secundaire taal: Engels.

## Infrastructuur

- **Server:** Hetzner CX42 — `ssh root@65.109.237.64`
- **Deployment:** Coolify op `http://65.109.237.64:8000`
- **GitHub:** `git@github.com:GetKlai/` (organisatie)
- **Domein:** getklai.com (registrar: Registrar.eu, DNS: Cloud86)

## Werkwijze

- Minimale wijzigingen: alleen wat gevraagd is
- Geen gedachtestreepjes (—) in content of code
- Geen display:none/block voor content switching

## Agent-configuratie

Deze repo bevat drie lagen agents:

- `agents/moai/` — MoAI-ADK agents (upstream, niet handmatig aanpassen)
- `agents/gtm/` — GTM content agents (Klai-eigendom, aangepast voor getklai.com)
- `agents/klai/` — Klai-eigen agents

Schrijfstijl voor alle content: zie `rules/gtm/klai-brand-voice.md`
