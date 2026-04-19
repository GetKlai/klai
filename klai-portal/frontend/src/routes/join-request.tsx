import { useState, useEffect } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { CheckCircle2 } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { authLogger } from '@/lib/logger'
import { apiFetch } from '@/lib/apiFetch'
import { fetchMe } from '@/lib/api-me'

export const Route = createFileRoute('/join-request')({
  component: JoinRequestPage,
})

function JoinRequestPage() {
  useLocale()
  const auth = useAuth()
  const [displayName, setDisplayName] = useState('')
  const [submitted, setSubmitted] = useState(false)

  // Prefetch email from /api/me to pre-fill display name
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: async ({ signal }) => fetchMe(signal),
    enabled: auth.isAuthenticated,
  })

  // Pre-fill display name from /api/me once loaded (only if the user hasn't typed anything)
  useEffect(() => {
    if (me && !displayName) {
      setDisplayName(me.name ?? me.email?.split('@')[0] ?? '')
    }
  }, [me]) // eslint-disable-line react-hooks/exhaustive-deps

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!auth.isAuthenticated) throw new Error('Not authenticated')
      return apiFetch<unknown>('/api/auth/join-request', { method: 'POST' })
    },
    onSuccess: () => {
      setSubmitted(true)
    },
    onError: (err) => {
      authLogger.error('Join request failed', { error: String(err) })
    },
  })

  const leftContent = (
    <>
      <h1 className="text-2xl font-semibold leading-tight">
        {m.no_account_hero_heading()}
        <br />
        <span className="text-[var(--color-rl-accent)]">{m.no_account_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-rl-cream)]">
        {m.no_account_hero_body()}
      </p>
    </>
  )

  if (submitted) {
    return (
      <AuthPageLayout leftContent={leftContent} showLocale>
        <div className="flex flex-col items-center gap-4 text-center">
          <CheckCircle2 className="h-10 w-10 text-[var(--color-success)]" />
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {m.join_request_success()}
          </p>
        </div>
      </AuthPageLayout>
    )
  }

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-[var(--color-foreground)]">
          {m.join_request_heading()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.join_request_body()}
        </p>
      </div>

      <div className="space-y-4">
        {me?.email && (
          <div className="space-y-1.5">
            <Label htmlFor="jr-email">Email</Label>
            <Input
              id="jr-email"
              type="email"
              value={me.email}
              disabled
              readOnly
              className="opacity-60"
            />
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="jr-name">Name</Label>
          <Input
            id="jr-name"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            maxLength={100}
          />
        </div>
      </div>

      {submitMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {submitMutation.error instanceof Error ? submitMutation.error.message : String(submitMutation.error)}
        </p>
      )}

      <Button
        onClick={() => submitMutation.mutate()}
        disabled={submitMutation.isPending}
        size="lg"
        className="w-full"
      >
        {m.join_request_submit()}
      </Button>
    </AuthPageLayout>
  )
}
