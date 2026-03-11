import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ArrowRight, CheckCircle, XCircle } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

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
  useLocale()
  const { code, userId, organization } = Route.useSearch()
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    if (!code || !userId || !organization) {
      setStatus('error')
      setErrorMessage(m.verify_error_missing_params())
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
          setErrorMessage(data?.detail ?? m.verify_error_invalid_link())
        }
      } catch {
        setStatus('error')
        setErrorMessage(m.verify_error_connection())
      }
    }

    verify()
  }, [code, userId, organization])

  const leftContent = (
    <>
      <h1 className="font-serif text-4xl font-bold leading-tight">
        {m.verify_hero_heading()}
        <br />
        <span className="text-[var(--color-purple-accent)]">{m.verify_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-sand-mid)]">
        {m.verify_hero_body()}
      </p>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      {status === 'loading' && (
        <div className="space-y-4 text-center">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.verify_loading()}
          </p>
        </div>
      )}

      {status === 'success' && (
        <div className="space-y-6 text-center">
          <div className="space-y-3">
            <CheckCircle className="mx-auto h-12 w-12 text-green-500" />
            <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              {m.verify_success_heading()}
            </h1>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.verify_success_body()}
            </p>
          </div>
          <Button size="lg" className="w-full gap-3" onClick={() => { window.location.href = '/' }}>
            {m.verify_success_cta()}
            <ArrowRight size={16} />
          </Button>
        </div>
      )}

      {status === 'error' && (
        <div className="space-y-6 text-center">
          <div className="space-y-3">
            <XCircle className="mx-auto h-12 w-12 text-red-400" />
            <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
              {m.verify_error_heading()}
            </h1>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {errorMessage ?? m.verify_error_invalid_link()}
            </p>
          </div>
          <p className="text-xs text-[var(--color-muted-foreground)]">
            {m.verify_error_hint()}{' '}
            <a href="mailto:support@getklai.com" className="text-[var(--color-purple-muted)] hover:underline">
              support@getklai.com
            </a>
            .
          </p>
        </div>
      )}
    </AuthPageLayout>
  )
}
