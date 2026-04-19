import { useAuth } from '@/lib/auth'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Loader2, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { DOCS_BASE, getOrgSlug, slugify } from '@/lib/kb-editor/tree-utils'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import { editorLogger } from '@/lib/logger'

interface SaveToKnowledgeModalProps {
  initialContent: string
  initialTitle: string
  onClose: () => void
  onSuccess: (pageSlug: string) => void
}

type AssertionMode = 'factual' | 'procedural' | 'belief' | 'hypothesis'

export function SaveToKnowledgeModal({
  initialContent, initialTitle, onClose, onSuccess,
}: SaveToKnowledgeModalProps) {
  const auth = useAuth()
  const token = auth.user?.access_token
  const orgSlug = getOrgSlug()
  const userUuid = auth.user?.profile?.sub ?? ''

  const [title, setTitle] = useState(initialTitle || (initialContent.split(/[.!?]/)[0]?.slice(0, 80) ?? ''))
  const [assertionMode, setAssertionMode] = useState<AssertionMode>('factual')
  const [tagsInput, setTagsInput] = useState('')
  const [sourceNote, setSourceNote] = useState('')
  const [savedSlug, setSavedSlug] = useState<string | null>(null)

  // Get page index for slug collision avoidance
  const { data: pageIndex = [] } = useQuery<Array<{ slug: string }>>({
    queryKey: ['docs-page-index', orgSlug, 'personal'],
    queryFn: async () => {
      try {
        return await apiFetch<Array<{ slug: string }>>(`${DOCS_BASE}/orgs/${orgSlug}/kbs/personal/page-index`, token)
      } catch {
        return []
      }
    },
    enabled: !!token,
  })

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!title.trim()) throw new Error('Titel is verplicht')

      // Generate unique slug with collision avoidance
      const baseSlug = slugify(title)
      const existingSlugs = new Set(pageIndex.map((p) => p.slug))
      let slug = `users/${userUuid}/${baseSlug}`
      let counter = 2
      while (existingSlugs.has(slug)) {
        slug = `users/${userUuid}/${baseSlug}-${counter++}`
      }
      const pageSlug = slug.replace(`users/${userUuid}/`, '')

      // V1 workaround: embed knowledge metadata as Markdown callout in content
      const tags = tagsInput.split(',').map((t) => t.trim()).filter(Boolean).slice(0, 5)
      const metaLines = [
        '> [!NOTE] Kennisbank metadata',
        `> **Type:** ${assertionMode}`,
      ]
      if (tags.length > 0) metaLines.push(`> **Labels:** ${tags.join(', ')}`)
      if (sourceNote) metaLines.push(`> **Bron:** ${sourceNote}`)
      metaLines.push('', '')

      const fullContent = metaLines.join('\n') + initialContent

      await apiFetch(
        `${DOCS_BASE}/orgs/${orgSlug}/kbs/personal/pages/users/${userUuid}/${pageSlug}`,
        token,
        {
          method: 'PUT',
          body: JSON.stringify({ title: title.trim(), content: fullContent, icon: '\u{1F4A1}' }),
        }
      )
      return { slug: pageSlug }
    },
    onSuccess: ({ slug }) => {
      editorLogger.info('Focus content saved to personal KB', { slug })
      setSavedSlug(slug)
      setTimeout(() => onSuccess(slug), 2000)
    },
    onError: (err) => {
      editorLogger.error('Save to KB failed', { error: err })
    },
  })

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 50,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        backgroundColor: 'rgba(0,0,0,0.4)',
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: 'var(--color-card)', border: '1px solid var(--color-border)',
          borderRadius: '0.75rem', padding: '1.5rem',
          width: '100%', maxWidth: '480px', boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 style={{ fontSize: '1.1rem', fontWeight: 700, color: 'var(--color-foreground)', marginBottom: '1rem' }}>
          {m.knowledge_save_modal_title()}
        </h2>

        {savedSlug ? (
          <div className="space-y-3">
            <p className="text-sm text-[var(--color-success)]">{m.knowledge_save_success()}</p>
            <a
              href="/app/docs"
              className="inline-flex items-center gap-1 text-sm text-[var(--color-accent)] hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              {m.knowledge_save_view_link()}
            </a>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="save-title">{m.knowledge_save_field_title()}</Label>
              <Input
                id="save-title"
                value={title}
                onChange={(e) => setTitle(e.target.value.slice(0, 80))}
                maxLength={80}
                autoFocus
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="save-mode">{m.knowledge_save_field_mode()}</Label>
              <Select
                id="save-mode"
                value={assertionMode}
                onChange={(e) => setAssertionMode(e.target.value as AssertionMode)}
                className="max-w-full"
              >
                <option value="factual">{m.knowledge_save_mode_factual()}</option>
                <option value="procedural">{m.knowledge_save_mode_procedural()}</option>
                <option value="belief">{m.knowledge_save_mode_belief()}</option>
                <option value="hypothesis">{m.knowledge_save_mode_hypothesis()}</option>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="save-tags">{m.knowledge_save_field_tags()}</Label>
              <Input
                id="save-tags"
                value={tagsInput}
                onChange={(e) => setTagsInput(e.target.value)}
                placeholder="tag1, tag2, tag3"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="save-source">{m.knowledge_save_field_source()}</Label>
              <textarea
                id="save-source"
                value={sourceNote}
                onChange={(e) => setSourceNote(e.target.value)}
                rows={2}
                style={{
                  width: '100%', padding: '0.5rem 0.75rem',
                  border: '1px solid var(--color-border)', borderRadius: '0.5rem',
                  fontSize: '0.875rem', resize: 'vertical',
                  background: 'var(--color-background)',
                  color: 'var(--color-foreground)',
                }}
              />
            </div>

            {saveMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {saveMutation.error instanceof Error ? saveMutation.error.message : m.knowledge_save_error()}
              </p>
            )}

            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <Button variant="ghost" onClick={onClose} disabled={saveMutation.isPending}>
                {m.docs_kb_cancel()}
              </Button>
              <Button
                onClick={() => saveMutation.mutate()}
                disabled={!title.trim() || saveMutation.isPending}
              >
                {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : m.knowledge_save_confirm()}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
