import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { AlertTriangle, Loader2, ArrowLeft } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/meetings/start')({
  component: StartMeetingPage,
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

  const platform = detectPlatform(meetingUrl)
  const isTeams = platform === 'Microsoft Teams'

  const startMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${BOTS_BASE}/meetings`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          meeting_url: meetingUrl,
          meeting_title: meetingTitle.trim() || null,
          consent_given: consentGiven,
        }),
      })
      if (res.status === 422) {
        const body = await res.json()
        throw new Error(body.detail ?? m.app_meetings_url_error())
      }
      if (res.status === 429) {
        throw new Error(m.app_meetings_limit_error())
      }
      if (!res.ok) throw new Error('Starten mislukt')
      return res.json() as Promise<{ id: string }>
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
    <div className="p-8 max-w-lg">
      <button
        onClick={() => navigate({ to: '/app/meetings' })}
        className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)] mb-6"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {m.app_meetings_back()}
      </button>

      <Card>
        <CardHeader>
          <CardTitle className="font-serif text-xl text-[var(--color-purple-deep)]">
            {m.app_meetings_start_title()}
          </CardTitle>
        </CardHeader>
        <CardContent>
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
