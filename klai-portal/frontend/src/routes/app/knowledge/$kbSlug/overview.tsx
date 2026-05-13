import { createFileRoute, redirect } from '@tanstack/react-router'

/**
 * Legacy /overview route — redirects to /insights, which now hosts the
 * Docs / Statistieken sections (extracted to `_components/KBOverviewSections.tsx`)
 * AND the taxonomy / coverage / sync-history blocks.
 */
export const Route = createFileRoute('/app/knowledge/$kbSlug/overview')({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/app/knowledge/$kbSlug/insights',
      params: { kbSlug: params.kbSlug },
    })
  },
})

// `KBOverviewSections` was extracted to
// `./_components/KBOverviewSections.tsx` so the Inzichten tab
// (`insights.tsx`) can consume it without violating
// klai/no-cross-route-import (insights.tsx and overview.tsx are
// both routes; routes do not import from each other).
