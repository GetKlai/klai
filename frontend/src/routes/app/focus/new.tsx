import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { useState } from 'react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/focus/new')({
  component: NewFocusPage,
})

const FOCUS_BASE = '/research/v1'

function NewFocusPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [scope, setScope] = useState<'personal' | 'org'>('personal')
  const [error, setError] = useState<string | null>(null)

  const createMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${FOCUS_BASE}/notebooks`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name, description: description || null, scope }),
      })
      if (!res.ok) throw new Error('Aanmaken mislukt')
      return res.json()
    },
    onSuccess: (data) => {
      navigate({ to: '/app/focus/$notebookId', params: { notebookId: data.id } })
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  return (
    <div className="p-8 space-y-6 max-w-xl">
      <div className="space-y-1">
        <button
          onClick={() => navigate({ to: '/app/focus' })}
          className="flex items-center gap-1.5 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors mb-4"
        >
          <ArrowLeft className="h-4 w-4" />
          {m.app_focus_back()}
        </button>
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.app_focus_new_title()}
        </h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{m.app_focus_new_notebook()}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">{m.app_focus_notebook_name_label()}</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Mijn notebook"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter' && name.trim()) createMutation.mutate() }}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">{m.app_focus_notebook_description_label()}</Label>
            <Input
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={m.app_focus_notebook_description_optional()}
            />
          </div>

          <div className="space-y-2">
            <Label>{m.app_focus_notebook_scope_label()}</Label>
            <div className="flex gap-1 p-1 bg-[var(--color-muted)]/40 rounded-lg w-fit">
              {(['personal', 'org'] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setScope(s)}
                  className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                    scope === s
                      ? 'bg-white shadow-sm text-[var(--color-purple-deep)]'
                      : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
                  }`}
                >
                  {s === 'personal' ? m.app_focus_notebook_scope_personal() : m.app_focus_notebook_scope_org()}
                </button>
              ))}
            </div>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <div className="flex gap-2 pt-1">
            <Button
              onClick={() => createMutation.mutate()}
              disabled={!name.trim() || createMutation.isPending}
            >
              {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {m.app_focus_create_submit()}
            </Button>
            <Button variant="outline" onClick={() => navigate({ to: '/app/focus' })}>
              {m.app_focus_create_cancel()}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
