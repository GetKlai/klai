import { createFileRoute, redirect } from '@tanstack/react-router'

// Zie ./index.tsx voor rationale. /app/templates/new → /app/instructions/new.
export const Route = createFileRoute('/app/templates/new')({
  beforeLoad: () => {
    throw redirect({ to: '/app/instructions/new' })
  },
})
