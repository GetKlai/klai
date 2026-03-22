import { createFileRoute, redirect } from '@tanstack/react-router'

// Redirects legacy /setup/2fa to the new /setup/mfa route.
export const Route = createFileRoute('/setup/2fa')({
  beforeLoad: () => {
    throw redirect({ to: '/setup/mfa', replace: true })
  },
  component: () => null,
})
