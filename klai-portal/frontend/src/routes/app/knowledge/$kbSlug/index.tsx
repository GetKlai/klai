import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/knowledge/$kbSlug/')({
  beforeLoad: ({ params }) => {
    // Default landing for a KB → bronnen (the actual data). Stats/dashboard
    // content lives under /insights now (no separate /overview view).
    throw redirect({
      to: '/app/knowledge/$kbSlug/sources',
      params: { kbSlug: params.kbSlug },
    })
  },
})
