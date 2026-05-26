import { createFileRoute, redirect } from '@tanstack/react-router'

// Instructies is samengevoegd met /app/instructions - zie ./index.tsx voor
// rationale. /admin/instructions/$slug/edit redirect naar de /app variant
// met het slug-pad behouden.
export const Route = createFileRoute('/admin/instructions/$slug/edit')({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/app/instructions/$slug/edit',
      params: { slug: params.slug },
    })
  },
})
