import { createFileRoute, redirect } from '@tanstack/react-router'

// Templates is samengevoegd met /app/templates — zie ./index.tsx voor
// rationale. /admin/templates/new redirect naar /app/templates/new.
export const Route = createFileRoute('/admin/templates/new')({
  beforeLoad: () => {
    throw redirect({ to: '/app/templates/new' })
  },
})
