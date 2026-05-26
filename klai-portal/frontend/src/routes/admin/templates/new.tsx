import { createFileRoute, redirect } from '@tanstack/react-router'

// Zie ./index.tsx voor rationale. /admin/templates/new → /app/instructions/new.
export const Route = createFileRoute('/admin/templates/new')({
  beforeLoad: () => {
    throw redirect({ to: '/app/instructions/new' })
  },
})
