import { createFileRoute, redirect } from '@tanstack/react-router'

// Templates is samengevoegd met /app/templates — zie ./index.tsx voor
// rationale. /admin/templates/$slug/edit redirect naar de /app variant
// met het slug-pad behouden.
export const Route = createFileRoute('/admin/templates/$slug/edit')({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/app/templates/$slug/edit',
      params: { slug: params.slug },
    })
  },
})
