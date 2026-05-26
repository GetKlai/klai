import { createFileRoute, redirect } from '@tanstack/react-router'

// Zie ./index.tsx voor rationale.
// /app/templates/$slug/edit → /app/instructions/$slug/edit (slug behouden).
export const Route = createFileRoute('/app/templates/$slug/edit')({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/app/instructions/$slug/edit',
      params: { slug: params.slug },
    })
  },
})
