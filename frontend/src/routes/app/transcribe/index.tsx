import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Tooltip } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Plus, Loader2, Pencil, Check, X, Trash2, Copy, CheckCheck, Mic, Download } from 'lucide-react'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/transcribe/')({
  component: TranscribePage,
})

const SCRIBE_BASE = '/scribe/v1'

interface TranscriptionItem {
  id: string
  name: string | null
  text: string
  language: string
  duration_seconds: number
  created_at: string
}

interface TranscriptionListResponse {
  items: TranscriptionItem[]
  total: number
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('nl-NL', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function TranscribePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState<string>('')
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  const { data, isLoading } = useQuery<TranscriptionListResponse>({
    queryKey: ['transcriptions', token],
    queryFn: async () => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions?limit=50`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Ophalen mislukt')
      return res.json()
    },
    enabled: !!token,
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transcriptions', token] })
    },
  })

  const renameMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string | null }) => {
      const res = await fetch(`${SCRIBE_BASE}/transcriptions/${id}`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name }),
      })
      if (!res.ok) throw new Error('Opslaan mislukt')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transcriptions', token] })
      setEditingId(null)
    },
  })

  function startEdit(item: TranscriptionItem) {
    setConfirmingDeleteId(null)
    setEditingId(item.id)
    setEditName(item.name ?? '')
  }

  function cancelEdit() {
    setEditingId(null)
    setEditName('')
  }

  function saveEdit(id: string) {
    renameMutation.mutate({ id, name: editName.trim() || null })
  }

  function downloadText(item: TranscriptionItem) {
    const blob = new Blob([item.text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${item.name ?? 'transcriptie'}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  async function copyText(item: TranscriptionItem) {
    await navigator.clipboard.writeText(item.text)
    setCopiedId(item.id)
    setTimeout(() => setCopiedId((prev) => (prev === item.id ? null : prev)), 2000)
  }

  const items = data?.items ?? []
  const filteredItems = search.trim()
    ? items.filter((item) => {
        const q = search.toLowerCase()
        return item.text.toLowerCase().includes(q) || (item.name?.toLowerCase().includes(q) ?? false)
      })
    : items

  const countLabel =
    data?.total === 1
      ? m.app_transcribe_count_one()
      : m.app_transcribe_count({ count: String(data?.total ?? 0) })

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.app_tool_transcribe_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && countLabel}
          </p>
        </div>
        <Button onClick={() => navigate({ to: '/app/transcribe/add' })}>
          <Plus className="mr-2 h-4 w-4" />
          {m.app_transcribe_add_button()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <Mic className="h-10 w-10 text-[var(--color-muted-foreground)] opacity-40" />
              <div className="space-y-1">
                <p className="font-medium text-[var(--color-purple-deep)]">
                  {m.app_transcribe_empty_heading()}
                </p>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  {m.app_transcribe_empty_body()}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => navigate({ to: '/app/transcribe/add' })}
                className="mt-2"
              >
                <Plus className="mr-2 h-3.5 w-3.5" />
                {m.app_transcribe_add_button()}
              </Button>
            </div>
          ) : (
            <>
              <div className="px-4 pt-3 pb-2 border-b border-[var(--color-border)]">
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={m.app_transcribe_search_placeholder()}
                  className="h-8 text-sm max-w-xs"
                />
              </div>
              {filteredItems.length === 0 ? (
                <p className="px-6 py-8 text-sm text-[var(--color-muted-foreground)]">
                  {m.app_transcribe_search_empty()}
                </p>
              ) : (
                <table className="w-full text-sm table-fixed">
                  <thead>
                    <tr className="border-b border-[var(--color-border)]">
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide w-1/2">
                        {m.app_transcribe_col_text()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_transcribe_col_words()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_transcribe_col_language()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_transcribe_col_duration()}
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                        {m.app_transcribe_col_date()}
                      </th>
                      <th className="px-6 py-3 w-32" />
                    </tr>
                  </thead>
                  <tbody>
                    {filteredItems.map((item, i) => {
                      const isEditing = editingId === item.id
                      const isConfirmingDelete = confirmingDeleteId === item.id
                      const isSaving = renameMutation.isPending && renameMutation.variables?.id === item.id
                      const isDeleting = deleteMutation.isPending && deleteMutation.variables === item.id
                      const isCopied = copiedId === item.id

                      return (
                        <tr
                          key={item.id}
                          className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}
                        >
                          <td className="px-6 py-3 text-[var(--color-purple-deep)] max-w-xs">
                            {isEditing ? (
                              <Input
                                value={editName}
                                onChange={(e) => setEditName(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') saveEdit(item.id)
                                  if (e.key === 'Escape') cancelEdit()
                                }}
                                disabled={isSaving}
                                autoFocus
                                className="h-7 text-sm"
                              />
                            ) : item.name ? (
                              <div>
                                <span className="block truncate font-medium">{item.name}</span>
                                <span className="block truncate text-xs text-[var(--color-muted-foreground)]">{item.text}</span>
                              </div>
                            ) : (
                              <span className="block truncate">{item.text}</span>
                            )}
                          </td>
                          <td className="px-6 py-3 text-[var(--color-muted-foreground)] tabular-nums">
                            {item.text.trim().split(/\s+/).filter(Boolean).length.toLocaleString()}
                          </td>
                          <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                            {item.language.toUpperCase()}
                          </td>
                          <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                            {formatDuration(item.duration_seconds)}
                          </td>
                          <td className="px-6 py-3 text-[var(--color-purple-deep)]">
                            {formatDate(item.created_at)}
                          </td>
                          <td className="px-6 py-3 w-32 text-right">
                            {isEditing ? (
                              <div className="flex items-center justify-end gap-1">
                                {isSaving ? (
                                  <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
                                ) : (
                                  <>
                                    <button
                                      onClick={() => saveEdit(item.id)}
                                      aria-label={m.app_transcribe_edit_save()}
                                      className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-success)] text-white transition-colors hover:opacity-90"
                                    >
                                      <Check className="h-3.5 w-3.5" />
                                    </button>
                                    <button
                                      onClick={cancelEdit}
                                      aria-label={m.app_transcribe_edit_cancel()}
                                      className="flex h-7 w-7 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-border)]"
                                    >
                                      <X className="h-3.5 w-3.5" />
                                    </button>
                                  </>
                                )}
                              </div>
                            ) : isConfirmingDelete ? (
                              <div className="flex items-center justify-end gap-1">
                                {isDeleting ? (
                                  <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
                                ) : (
                                  <>
                                    <button
                                      onClick={() => {
                                        setConfirmingDeleteId(null)
                                        deleteMutation.mutate(item.id)
                                      }}
                                      aria-label={m.app_transcribe_delete_confirm()}
                                      className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-destructive)] text-white transition-colors hover:opacity-90"
                                    >
                                      <Check className="h-3.5 w-3.5" />
                                    </button>
                                    <button
                                      onClick={() => setConfirmingDeleteId(null)}
                                      aria-label={m.app_transcribe_delete_cancel()}
                                      className="flex h-7 w-7 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] transition-colors hover:bg-[var(--color-border)]"
                                    >
                                      <X className="h-3.5 w-3.5" />
                                    </button>
                                  </>
                                )}
                              </div>
                            ) : (
                              <div className="flex items-center justify-end gap-1">
                                <Tooltip label={m.app_transcribe_edit_label()}>
                                  <button
                                    onClick={() => startEdit(item)}
                                    aria-label={m.app_transcribe_edit_label()}
                                    className="flex h-7 w-7 items-center justify-center text-[var(--color-muted-foreground)] transition-colors hover:text-[var(--color-purple-deep)]"
                                  >
                                    <Pencil className="h-3.5 w-3.5" />
                                  </button>
                                </Tooltip>
                                <Tooltip label={m.app_transcribe_copy_label()}>
                                  <button
                                    onClick={() => copyText(item)}
                                    aria-label={m.app_transcribe_copy_label()}
                                    className="flex h-7 w-7 items-center justify-center text-[var(--color-muted-foreground)] transition-colors hover:text-[var(--color-purple-deep)]"
                                  >
                                    {isCopied ? (
                                      <CheckCheck className="h-3.5 w-3.5 text-[var(--color-success)]" />
                                    ) : (
                                      <Copy className="h-3.5 w-3.5" />
                                    )}
                                  </button>
                                </Tooltip>
                                <Tooltip label={m.app_transcribe_download_label()}>
                                  <button
                                    onClick={() => downloadText(item)}
                                    aria-label={m.app_transcribe_download_label()}
                                    className="flex h-7 w-7 items-center justify-center text-[var(--color-muted-foreground)] transition-colors hover:text-[var(--color-purple-deep)]"
                                  >
                                    <Download className="h-3.5 w-3.5" />
                                  </button>
                                </Tooltip>
                                <Tooltip label={m.app_transcribe_delete_label()}>
                                  <button
                                    onClick={() => { cancelEdit(); setConfirmingDeleteId(item.id) }}
                                    aria-label={m.app_transcribe_delete_label()}
                                    className="flex h-7 w-7 items-center justify-center text-[var(--color-muted-foreground)] transition-colors hover:text-[var(--color-destructive)]"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>
                                </Tooltip>
                              </div>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
