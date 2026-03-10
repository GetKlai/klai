import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight, CheckCircle, XCircle } from 'lucide-react'
import { setLocale } from '@/paraglide/runtime'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

function getInitialLocale() {
  const saved = localStorage.getItem('klai-locale')
  const locale = saved === 'en' ? 'en' : 'nl'
  setLocale(locale)
  return locale
}

type SearchParams = {
  code?: string
  userId?: string
  organization?: string
}

export const Route = createFileRoute('/verify')({
  validateSearch: (search: Record<string, unknown>): SearchParams => ({
    code: typeof search.code === 'string' ? search.code : undefined,
    userId: typeof search.userId === 'string' ? search.userId : undefined,
    organization: typeof search.organization === 'string' ? search.organization : undefined,
  }),
  component: VerifyEmailPage,
})

function VerifyEmailPage() {
  getInitialLocale()
  const { code, userId, organization } = Route.useSearch()
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    if (!code || !userId || !organization) {
      setStatus('error')
      setErrorMessage('Ongeldige verificatielink.')
      return
    }

    async function verify() {
      try {
        const resp = await fetch(`${API_BASE}/api/auth/verify-email`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, user_id: userId, org_id: organization }),
        })
        if (resp.ok) {
          setStatus('success')
        } else {
          const data = await resp.json().catch(() => ({}))
          setStatus('error')
          setErrorMessage(data?.detail ?? 'Verificatie mislukt.')
        }
      } catch {
        setStatus('error')
        setErrorMessage('Er is een verbindingsfout opgetreden. Probeer het later opnieuw.')
      }
    }

    verify()
  }, [code, userId, organization])

  return (
    <div className="flex min-h-screen bg-[var(--color-off-white)]">
      {/* Left panel — branding */}
      <div className="hidden lg:flex lg:w-1/2 flex-col justify-between bg-[var(--color-purple-deep)] p-12 text-[var(--color-sand-light)]">
        <div>
          <img src="/klai-logo-white.svg" alt="Klai" className="h-7 w-auto block" />
        </div>

        <div className="space-y-6 my-auto">
          <h1 className="font-serif text-4xl font-bold leading-tight">
            Bijna klaar.
            <br />
            <span className="text-[var(--color-purple-accent)]">Bevestig je e-mail.</span>
          </h1>
          <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
            We bevestigen je e-mailadres om je account te beveiligen en je toegang te geven tot Klai.
          </p>
        </div>
      </div>

      {/* Right panel */}
      <div className="flex w-full flex-col items-center justify-center px-8 lg:w-1/2">
        <div className="w-full max-w-sm space-y-6">
          <div className="flex justify-center lg:hidden">
            <img src="/klai-logo.svg" alt="Klai" className="h-7 w-auto" />
          </div>

          {status === 'loading' && (
            <div className="space-y-4 text-center">
              <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
              <p className="text-sm text-[var(--color-muted-foreground)]">
                Je e-mailadres wordt bevestigd...
              </p>
            </div>
          )}

          {status === 'success' && (
            <div className="space-y-6 text-center">
              <div className="space-y-3">
                <CheckCircle className="mx-auto h-12 w-12 text-green-500" />
                <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
                  E-mailadres bevestigd
                </h1>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  Je account is actief. Je kunt nu inloggen.
                </p>
              </div>
              <Button size="lg" className="w-full gap-3" onClick={() => { window.location.href = '/' }}>
                Inloggen
                <ArrowRight size={16} />
              </Button>
            </div>
          )}

          {status === 'error' && (
            <div className="space-y-6 text-center">
              <div className="space-y-3">
                <XCircle className="mx-auto h-12 w-12 text-red-400" />
                <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
                  Verificatie mislukt
                </h1>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  {errorMessage ?? 'Deze link is ongeldig of verlopen.'}
                </p>
              </div>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                Meld je opnieuw aan om een nieuwe verificatiemail te ontvangen, of neem contact op via{' '}
                <a href="mailto:support@getklai.com" className="text-[var(--color-purple-muted)] hover:underline">
                  support@getklai.com
                </a>
                .
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
