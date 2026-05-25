/**
 * SPEC-PORTAL-SOURCES-RENAME-001 REQ-1 - legacy URL redirect.
 *
 * /app/knowledge/<slug>/bronnen → /app/knowledge/<slug>/sources
 *
 * Honours external bookmarks and any cached deep-link from before the
 * 2026-05-12 rename. No component is rendered; the redirect fires in
 * beforeLoad so the user never sees the legacy path land.
 *
 * REMOVE schedule: after 30 days of zero hits in VictoriaLogs
 * (`service:caddy AND path:"/app/knowledge/" AND path:"/bronnen"`),
 * delete this file. The TanStack file router will then 404 on the
 * old path, which is the correct end state.
 */
import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/knowledge/$kbSlug/bronnen')({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/app/knowledge/$kbSlug/sources',
      params: { kbSlug: params.kbSlug },
      replace: true,
    })
  },
})
