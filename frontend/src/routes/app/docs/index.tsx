import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Plus, Loader2, BookMarked, Globe, Lock, Pencil, Trash2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/docs/')({
  component: DocsPage,
})

const DOCS_BASE = '/docs/api'

function getOrgSlug(): string {
  return window.location.hostname.split('.')[0]
}

interface KnowledgeBase {
  id: string
  slug: string
  name: string
  visibility: 'public' | 'private'
}

interface DeleteModalProps {
  kb: KnowledgeBase
  onCancel: () => void
  onConfirm: () => void
  isDeleting: boolean
}

function DeleteModal({ kb, onCancel, onConfirm, isDeleting }: DeleteModalProps) {
  const [confirmName, setConfirmName] = useState('')
  const canDelete = confirmName === kb.name

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 50,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(0,0,0,0.4)',
      }}
      onClick={onCancel}
    >
      <div
        style={{
          background: 'var(--color-card)',
          border: '1px solid var(--color-border)',
          borderRadius: '0.75rem',
          padding: '1.5rem',
          width: '100%',
          maxWidth: '420px',
          boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          style={{
            fontSize: '1.1rem',
            fontWeight: 700,
            color: 'var(--color-purple-deep)',
            marginBottom: '0.75rem',
          }}
        >
          {m.docs_kb_delete_modal_title()}
        </h2>
        <p
          style={{
            fontSize: '0.875rem',
            color: 'var(--color-muted-foreground)',
            marginBottom: '1rem',
            lineHeight: 1.5,
          }}
        >
          Dit verwijdert alle pagina's en kan niet ongedaan worden gemaakt. Typ{' '}
          <strong style={{ color: 'var(--color-purple-deep)' }}>{kb.name}</strong>{' '}
          ter bevestiging.
        </p>
        <div style={{ marginBottom: '1.25rem' }}>
          <Input
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
            placeholder={m.docs_kb_delete_name_placeholder()}
            autoFocus
          />
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
          <Button variant="ghost" onClick={onCancel} disabled={isDeleting}>
            {m.docs_kb_delete_cancel_action()}
          </Button>
          <Button
            onClick={onConfirm}
            disabled={!canDelete || isDeleting}
            style={{
              backgroundColor: canDelete ? 'var(--color-destructive)' : undefined,
              color: canDelete ? 'white' : undefined,
            }}
          >
            {isDeleting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              m.docs_kb_delete_confirm_action()
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}

function DocsPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const orgSlug = getOrgSlug()
  const queryClient = useQueryClient()

  const [deletingKb, setDeletingKb] = useState<KnowledgeBase | null>(null)

  const { data: kbs = [], isLoading, error } = useQuery<KnowledgeBase[]>({
    queryKey: ['docs-kbs', orgSlug],
    queryFn: async () => {
      const res = await fetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Laden mislukt')
      return res.json()
    },
    enabled: !!token,
  })

  const deleteMutation = useMutation({
    mutationFn: async (kb: KnowledgeBase) => {
      const res = await fetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kb.slug}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['docs-kbs', orgSlug] })
      setDeletingKb(null)
    },
  })

  const countLabel =
    kbs.length === 1
      ? m.docs_kbs_count_one()
      : m.docs_kbs_count({ count: String(kbs.length) })

  return (
    <div className="p-8 space-y-6">
      {deletingKb && (
        <DeleteModal
          kb={deletingKb}
          onCancel={() => setDeletingKb(null)}
          onConfirm={() => deleteMutation.mutate(deletingKb)}
          isDeleting={deleteMutation.isPending}
        />
      )}

      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.docs_kbs_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && countLabel}
          </p>
        </div>
        <Button onClick={() => navigate({ to: '/app/docs/new' })}>
          <Plus className="mr-2 h-4 w-4" />
          {m.docs_kbs_new()}
        </Button>
      </div>

      {(error || deleteMutation.error) && (
        <p className="text-sm text-[var(--color-destructive)]">
          {error instanceof Error
            ? error.message
            : deleteMutation.error instanceof Error
              ? deleteMutation.error.message
              : 'Laden mislukt'}
        </p>
      )}

      <Card>
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
            </div>
          ) : kbs.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <BookMarked className="h-10 w-10 text-[var(--color-muted-foreground)] opacity-40" />
              <div className="space-y-1">
                <p className="font-medium text-[var(--color-purple-deep)]">
                  {m.docs_kb_empty_heading()}
                </p>
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  {m.docs_kb_empty_body()}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => navigate({ to: '/app/docs/new' })}
                className="mt-2"
              >
                <Plus className="mr-2 h-3.5 w-3.5" />
                {m.docs_kbs_new()}
              </Button>
            </div>
          ) : (
            <table className="w-full text-sm table-fixed">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.docs_kb_name_label()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide w-32">
                    Zichtbaarheid
                  </th>
                  <th className="px-3 py-3 w-20" />
                </tr>
              </thead>
              <tbody>
                {kbs.map((kb, i) => (
                  <tr
                    key={kb.id}
                    className={
                      i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'
                    }
                  >
                    <td
                      className="px-6 py-3 text-[var(--color-purple-deep)] font-medium cursor-pointer hover:underline"
                      onClick={() =>
                        navigate({ to: '/app/docs/$kbSlug', params: { kbSlug: kb.slug } })
                      }
                    >
                      {kb.name}
                    </td>
                    <td className="px-6 py-3">
                      <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-muted-foreground)]">
                        {kb.visibility === 'public' ? (
                          <Globe size={12} />
                        ) : (
                          <Lock size={12} />
                        )}
                        {kb.visibility === 'public'
                          ? m.docs_kb_visibility_public()
                          : m.docs_kb_visibility_private()}
                      </span>
                    </td>
                    <td className="px-3 py-3 w-20 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Tooltip label={m.docs_kb_edit_label()}>
                          <button
                            onClick={() =>
                              navigate({ to: '/app/docs/$kbSlug_/edit', params: { kbSlug: kb.slug } })
                            }
                            aria-label={m.docs_kb_edit_label()}
                            className="flex h-7 w-7 items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                        </Tooltip>
                        <Tooltip label={m.docs_kb_delete_label()}>
                          <button
                            onClick={() => setDeletingKb(kb)}
                            aria-label={m.docs_kb_delete_label()}
                            className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </Tooltip>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
