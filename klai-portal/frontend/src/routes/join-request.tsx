import { useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { CheckCircle2 } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { authLogger } from '@/lib/logger'

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
    queryFn: async () => {
      const res = await fetch('/api/me', {
        headers: { Authorization: `Bearer ${auth.user!.access_token}` },
      })
      if (!res.ok) return null
      return res.json() as Promise<{ email?: string; display_name?: string }>
    },
    enabled: !!auth.user,
  })

  // Pre-fill display name from /api/me once loaded
  const prefillName = me?.display_name ?? me?.email?.split('@')[0] ?? ''

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!auth.user) throw new Error('Not authenticated')
      const res = await fetch('/api/auth/join-request', {
        method: 'POST',
        headers: { Authorization: `Bearer ${auth.user.access_token}` },
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error((data as { detail?: string }).detail ?? `HTTP ${res.status}`)
      }
      return res.json()
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
            value={displayName || prefillName}
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
