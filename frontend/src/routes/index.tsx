import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { Button } from '@/components/ui/button'
import { ArrowRight, Shield, Lock } from 'lucide-react'

export const Route = createFileRoute('/')({
  component: LoginPage,
})

function LoginPage() {
  const auth = useAuth()

  if (auth.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-off-white)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
      </div>
    )
  }

  if (auth.isAuthenticated) {
    const isAdmin = sessionStorage.getItem('klai:isAdmin') === 'true'
    window.location.replace(isAdmin ? '/admin' : '/app')
    return null
  }

  return (
    <div className="flex min-h-screen bg-[var(--color-off-white)]">
      {/* Left panel — branding */}
      <div className="hidden lg:flex lg:w-1/2 flex-col justify-between bg-[var(--color-purple-deep)] p-12 text-[var(--color-sand-light)]">
        {/* Logo */}
        <div>
          <span className="font-serif text-2xl font-bold">Klai</span>
        </div>

        {/* Tagline */}
        <div className="space-y-6">
          <h1 className="font-serif text-4xl font-bold leading-tight">
            AI die van jou is.
            <br />
            <span className="text-[var(--color-purple-accent)]">Niet van Silicon Valley.</span>
          </h1>
          <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
            Jouw werkruimte. Jouw data. Draait op Europese servers, nooit gedeeld, altijd aantoonbaar privé.
          </p>

          {/* Trust signals */}
          <div className="flex flex-col gap-3 pt-4">
            <div className="flex items-center gap-3 text-sm text-[var(--color-sand-mid)]">
              <Shield size={16} className="shrink-0 text-[var(--color-purple-accent)]" />
              Europese infrastructuur — data verlaat nooit de EU
            </div>
            <div className="flex items-center gap-3 text-sm text-[var(--color-sand-mid)]">
              <Lock size={16} className="shrink-0 text-[var(--color-purple-accent)]" />
              Open source modellen, aantoonbaar privé
            </div>
          </div>
        </div>

        <p className="text-xs text-[var(--color-sand-mid)] opacity-50">
          © {new Date().getFullYear()} Klai
        </p>
      </div>

      {/* Right panel — login */}
      <div className="flex w-full flex-col items-center justify-center px-8 lg:w-1/2">
        <div className="w-full max-w-sm space-y-8">
          {/* Mobile logo */}
          <div className="lg:hidden">
            <span className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">Klai</span>
          </div>

          <div className="space-y-2">
            <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              Welkom terug
            </h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              Log in op jouw werkruimte
            </p>
          </div>

          <div className="space-y-4">
            <Button
              size="lg"
              className="w-full gap-3"
              onClick={() => auth.signinRedirect()}
            >
              Inloggen
              <ArrowRight size={16} />
            </Button>

            <p className="text-center text-xs text-[var(--color-muted-foreground)]">
              Nog geen account?{' '}
              <a
                href="/signup"
                className="font-medium text-[var(--color-purple-muted)] hover:underline"
              >
                Aanmelden
              </a>
            </p>
          </div>

          <p className="text-center text-xs text-[var(--color-muted-foreground)] opacity-60">
            Door in te loggen ga je akkoord met onze{' '}
            <a href="https://getklai.com/privacy" className="hover:underline">
              privacyverklaring
            </a>
          </p>
        </div>
      </div>
    </div>
  )
}
