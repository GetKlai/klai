import { createFileRoute, redirect } from '@tanstack/react-router'

// Instructies is samengevoegd met /app/instructions - zie ./index.tsx voor
// rationale. /admin/instructions/new redirect naar /app/instructions/new.
export const Route = createFileRoute('/admin/instructions/new')({
  beforeLoad: () => {
    throw redirect({ to: '/app/instructions/new' })
  },
})
