import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { AlertTriangle, Loader2, ArrowLeft, Info, X } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/app/meetings/start')({
  component: () => (
    <ProductGuard product="scribe">
      <StartMeetingPage />
    </ProductGuard>
  ),
})

const BOTS_BASE = '/api/bots'

function detectPlatform(url: string): string | null {
  if (/meet\.google\.com/i.test(url)) return 'Google Meet'
  if (/zoom\.us\/j\//i.test(url)) return 'Zoom'
  if (/teams\.microsoft\.com/i.test(url)) return 'Microsoft Teams'
  return null
}

function StartMeetingPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const [meetingUrl, setMeetingUrl] = useState('')
  const [meetingTitle, setMeetingTitle] = useState('')
  const [consentGiven, setConsentGiven] = useState(false)
  const [urlError, setUrlError] = useState<string | null>(null)
  const [showOnboarding, setShowOnboarding] = useState(() => {
    try {
      return localStorage.getItem('meetings_start_onboarding_dismissed') !== '1'
    } catch {
      return true
    }
  })

  function dismissOnboarding() {
    try {
      localStorage.setItem('meetings_start_onboarding_dismissed', '1')
    } catch { /* localStorage unavailable in sandboxed contexts */ }
    setShowOnboarding(false)
  }

  const platform = detectPlatform(meetingUrl)
  const isTeams = platform === 'Microsoft Teams'

  const startMutation = useMutation({
    mutationFn: async () => {
      return apiFetch<{ id: string }>(`${BOTS_BASE}/meetings`, token, {
        method: 'POST',
        body: JSON.stringify({
          meeting_url: meetingUrl,
          meeting_title: meetingTitle.trim() || null,
          consent_given: consentGiven,
        }),
      })
    },
    onSuccess: (meeting) => {
      void navigate({ to: '/app/meetings/$meetingId', params: { meetingId: meeting.id } })
    },
    onError: (err: Error) => {
      setUrlError(err.message)
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setUrlError(null)
    startMutation.mutate()
  }

  return (
    <div className="p-12 max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.app_meetings_start_title()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={() => navigate({ to: '/app/transcribe' })}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.app_meetings_back()}
        </Button>
      </div>

      {showOnboarding && (
        <div className="mb-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-secondary)] p-4">
          <div className="flex items-start gap-3">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-purple-accent)]" />
            <div className="flex-1 space-y-3 text-sm">
              <p className="font-medium text-[var(--color-purple-deep)]">
                {m.app_meetings_start_onboarding_title()}
              </p>
              <div className="space-y-0.5">
                <p className="text-xs font-medium text-[var(--color-foreground)]">
                  {m.app_meetings_start_onboarding_url_heading()}
                </p>
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  {m.app_meetings_start_onboarding_url_body()}
                </p>
              </div>
              <div className="space-y-0.5">
                <p className="text-xs font-medium text-[var(--color-foreground)]">
                  {m.app_meetings_start_onboarding_invite_heading()}
                </p>
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  {m.app_meetings_start_onboarding_invite_body()}
                </p>
              </div>
              <button
                onClick={dismissOnboarding}
                className="text-xs font-medium text-[var(--color-purple-accent)] hover:text-[var(--color-purple-deep)] transition-colors"
              >
                {m.app_meetings_start_onboarding_dismiss()}
              </button>
            </div>
            <button
              onClick={dismissOnboarding}
              className="shrink-0 rounded p-1 text-[var(--color-muted-foreground)] hover:bg-[var(--color-border)] hover:text-[var(--color-foreground)] transition-colors"
              aria-label={m.app_meetings_start_onboarding_dismiss()}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="meeting-url">{m.app_meetings_url_label()}</Label>
              <Input
                id="meeting-url"
                type="url"
                value={meetingUrl}
                onChange={(e) => {
                  setMeetingUrl(e.target.value)
                  setUrlError(null)
                }}
                placeholder={m.app_meetings_url_placeholder()}
                required
                className={urlError ? 'border-[var(--color-destructive)]' : ''}
              />
              {platform && (
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  Platform:{' '}
                  <span className="font-medium text-[var(--color-purple-deep)]">{platform}</span>
                </p>
              )}
              {urlError && (
                <p className="text-xs text-[var(--color-destructive)]">{urlError}</p>
              )}
            </div>

            {isTeams && (
              <div className="flex gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{m.app_meetings_teams_warning()}</span>
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="meeting-title">{m.app_meetings_title_label()}</Label>
              <Input
                id="meeting-title"
                type="text"
                value={meetingTitle}
                onChange={(e) => setMeetingTitle(e.target.value)}
                placeholder={m.app_meetings_title_placeholder()}
              />
            </div>

            <div className="flex items-start gap-3 rounded-md border border-[var(--color-border)] bg-[var(--color-secondary)] p-4">
              <input
                id="consent"
                type="checkbox"
                checked={consentGiven}
                onChange={(e) => setConsentGiven(e.target.checked)}
                className="mt-1 h-4 w-4 shrink-0 rounded border-[var(--color-border)] accent-[var(--color-purple-accent)]"
              />
              <label
                htmlFor="consent"
                className="text-sm text-[var(--color-purple-deep)] cursor-pointer leading-relaxed"
              >
                {m.app_meetings_consent_label()}
              </label>
            </div>

            <Button
              type="submit"
              className="w-full"
              disabled={!consentGiven || startMutation.isPending}
            >
              {startMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {m.app_meetings_submitting()}
                </>
              ) : (
                m.app_meetings_submit()
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
