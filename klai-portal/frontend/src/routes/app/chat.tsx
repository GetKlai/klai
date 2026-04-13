import { createFileRoute, redirect } from '@tanstack/react-router'

// Chat now lives at /app/ (the homepage). This route exists for backward compatibility.
export const Route = createFileRoute('/app/chat')({
  beforeLoad: () => {
    throw redirect({ to: '/app/' })
  },
})
