/**
 * SPEC-PORTAL-KENNIS-002 Track 1 - /advanced redirects to /insights.
 */
import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/knowledge/$kbSlug/advanced')({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/app/knowledge/$kbSlug/insights',
      params: { kbSlug: params.kbSlug },
    })
  },
  component: () => null,
})
