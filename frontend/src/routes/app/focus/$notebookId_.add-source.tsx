import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { ArrowLeft, Loader2, Upload } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/focus/$notebookId_/add-source')({
  component: AddSourcePage,
})

const FOCUS_BASE = '/research/v1'

type AddTab = 'file' | 'url'

function AddSourcePage() {
  const { notebookId } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [addTab, setAddTab] = useState<AddTab>('file')
  const [urlInput, setUrlInput] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  function detectUrlType(url: string): 'url' | 'youtube' {
    try {
      const u = new URL(url)
      if (
        u.hostname === 'www.youtube.com' ||
        u.hostname === 'youtube.com' ||
        u.hostname === 'm.youtube.com' ||
        u.hostname === 'youtu.be'
      ) {
        return 'youtube'
      }
    } catch {}
    return 'url'
  }

  const addFileMutation = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/sources`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      })
      if (!res.ok) throw new Error('Uploaden mislukt')
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['focus-sources', notebookId, token] })
      navigate({ to: '/app/focus/$notebookId', params: { notebookId } })
    },
    onError: (err: Error) => setAddError(err.message),
  })

  const addUrlMutation = useMutation({
    mutationFn: async (url: string) => {
      const res = await fetch(`${FOCUS_BASE}/notebooks/${notebookId}/sources/url`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url, type: detectUrlType(url) }),
      })
      if (!res.ok) throw new Error('Toevoegen mislukt')
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['focus-sources', notebookId, token] })
      navigate({ to: '/app/focus/$notebookId', params: { notebookId } })
    },
    onError: (err: Error) => setAddError(err.message),
  })

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.app_focus_add_source()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/app/focus/$notebookId', params: { notebookId } })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.app_focus_create_cancel()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6 space-y-4">
          {/* Tabs */}
          <div className="flex gap-1 p-1 bg-[var(--color-muted)]/40 rounded-lg w-fit">
            {(['file', 'url'] as AddTab[]).map((tab) => (
              <button
                key={tab}
                onClick={() => { setAddTab(tab); setAddError(null) }}
                className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                  addTab === tab
                    ? 'bg-white shadow-sm text-[var(--color-purple-deep)]'
                    : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
                }`}
              >
                {tab === 'file' ? m.app_focus_source_add_tab_file() : m.app_focus_source_add_tab_url()}
              </button>
            ))}
          </div>

          {addTab === 'file' && (
            <div
              className={`cursor-pointer rounded-lg border-2 border-dashed p-6 text-center transition-colors ${
                dragging
                  ? 'border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/5'
                  : 'border-[var(--color-border)] hover:border-[var(--color-purple-accent)]/50'
              }`}
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                const f = e.dataTransfer.files[0]
                if (f) addFileMutation.mutate(f)
              }}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.docx,.txt,.md"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) addFileMutation.mutate(f)
                }}
              />
              {addFileMutation.isPending ? (
                <div className="flex items-center justify-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
                  <span className="text-sm text-[var(--color-muted-foreground)]">
                    {m.app_focus_source_uploading()}
                  </span>
                </div>
              ) : (
                <div>
                  <Upload className="mx-auto mb-2 h-6 w-6 text-[var(--color-muted-foreground)]" />
                  <p className="text-sm font-medium text-[var(--color-purple-deep)]">{m.app_focus_source_file_hint()}</p>
                  <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">PDF, DOCX, TXT, MD</p>
                </div>
              )}
            </div>
          )}

          {addTab === 'url' && (
            <div className="flex gap-2">
              <Input
                type="url"
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                placeholder={m.app_focus_source_url_placeholder()}
                className="flex-1"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && urlInput.trim())
                    addUrlMutation.mutate(urlInput.trim())
                }}
              />
              <Button
                onClick={() => addUrlMutation.mutate(urlInput.trim())}
                disabled={!urlInput.trim() || addUrlMutation.isPending}
              >
                {addUrlMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  m.app_focus_source_add_button()
                )}
              </Button>
            </div>
          )}

          {addError && (
            <p className="text-sm text-[var(--color-destructive)]">{addError}</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
