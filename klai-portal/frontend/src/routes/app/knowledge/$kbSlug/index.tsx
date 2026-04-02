import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/knowledge/$kbSlug/')({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/app/knowledge/$kbSlug/overview',
      params: { kbSlug: params.kbSlug },
    })
  },
})
