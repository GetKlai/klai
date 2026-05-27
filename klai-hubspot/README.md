# Klai HubSpot Integrations

This directory contains HubSpot-specific extension projects. Keep these projects separate from Klai core services because they use the HubSpot Projects lifecycle (`hs project upload`, `hs project deploy`) and HubSpot UI extension dependencies.

## Projects

- `klai-email-support/` — HubSpot Help Desk app card for Klai-generated support email drafts.

## Accounts

- Sandbox: `Sandbox environment | Voys` — Hub ID `147785398`
- Live: `Voys` — Hub ID `5604529`

Do not upload or deploy this project without explicitly targeting the sandbox during POC work.

```sh
hs project upload --account 147785398
```

Production/live deployment is intentionally out of scope until the sandbox POC is approved.

## Current POC Status

- Project: `klai-email-support`
- Sandbox profile: `sandbox`
- First sandbox deploy: build #1
- Deploy URL: `https://app.hubspot.com/developer-projects/147785398/project/klai-email-support/activity/deploy/1`

The current POC card appears in Help Desk ticket sidebars, reads the current ticket subject/content, calls the Klai Partner API with an OpenAI-compatible chat-completions request plus the Klai `knowledge` extension, shows a generated draft preview with sources, and opens HubSpot's email composer when a contact association is available.

## Security

Personal access keys and developer keys must never be committed. If a key is pasted into chat or logs, rotate it after the POC.
