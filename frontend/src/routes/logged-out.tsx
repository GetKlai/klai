import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'

export const Route = createFileRoute('/logged-out')({
  component: LoggedOutPage,
})

function LoggedOutPage() {
  const auth = useAuth()

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[var(--color-off-white)]">
      <p className="text-sm text-gray-600">Je bent uitgelogd</p>
      <button
        onClick={() => auth.signinRedirect()}
        className="rounded bg-[var(--color-purple-accent)] px-4 py-2 text-sm text-white hover:opacity-90"
      >
        Opnieuw inloggen
      </button>
    </div>
  )
}
