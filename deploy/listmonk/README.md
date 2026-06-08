# Klai listmonk templates

Source-controlled HTML for the templates used in listmonk at `mailing.getklai.com`.

## Templates

| Slug | Type | Purpose |
| --- | --- | --- |
| `campaign` | `campaign` | Reusable Klai shell for regular listmonk campaigns. Select this for mails like product updates or beta-feedback requests. |
| `onboarding_invite` | `tx` | Transactional onboarding invite sent by portal-api via `/api/tx` with `LISTMONK_TX_ONBOARDING_TEMPLATE_ID`. |

Both templates use the canonical Klai logo URL: `https://getklai.com/logo-black.svg`.

## Sync

```bash
LISTMONK_URL=https://mailing.getklai.com \
LISTMONK_API_USER=... \
LISTMONK_API_TOKEN=... \
python deploy/scripts/listmonk-sync-templates.py --dry-run
```

Remove `--dry-run` to apply. If `LISTMONK_TX_ONBOARDING_TEMPLATE_ID` is set,
the onboarding transactional template is updated by ID. Otherwise templates are
matched by `name` + `type`, and created when missing.

To make the campaign shell listmonk's default campaign template:

```bash
python deploy/scripts/listmonk-sync-templates.py --only campaign --set-campaign-default
```
