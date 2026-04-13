import { useEffect, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, ChevronDown } from 'lucide-react'

import { apiFetch } from '@/lib/apiFetch'
import { chatKbLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'

interface KBPref {
  kb_retrieval_enabled: boolean
  kb_personal_enabled: boolean
  kb_slugs_filter: string[] | null
  kb_narrow: boolean
  kb_pref_version: number
}

interface OrgKB {
  slug: string
  name: string
}

export function KBScopeBar() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const { data: pref } = useQuery<KBPref>({
    queryKey: ['kb-preference'],
    queryFn: async () => apiFetch<KBPref>('/api/app/account/kb-preference', token),
    enabled: !!token,
  })

  const { data: kbsData } = useQuery<{ knowledge_bases: OrgKB[] }>({
    queryKey: ['org-kbs-for-bar'],
    queryFn: async () => apiFetch<{ knowledge_bases: OrgKB[] }>('/api/app/knowledge-bases?owner_type=org', token),
    enabled: !!token,
  })

  const orgKbs = kbsData?.knowledge_bases ?? []

  const mutation = useMutation({
    mutationFn: async (patch: Partial<Omit<KBPref, 'kb_pref_version'>>) => {
      return apiFetch<KBPref>('/api/app/account/kb-preference', token, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      })
    },
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: ['kb-preference'] })
      const previous = queryClient.getQueryData<KBPref>(['kb-preference'])
      if (previous) {
        queryClient.setQueryData<KBPref>(['kb-preference'], { ...previous, ...patch })
      }
      return { previous }
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['kb-preference'], data)
      chatKbLogger.info('KB preference saved', { version: data.kb_pref_version })
    },
    onError: (err, _patch, context) => {
      if (context?.previous) {
        queryClient.setQueryData(['kb-preference'], context.previous)
      }
      chatKbLogger.error('KB preference save failed', { err })
    },
  })

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) {
      document.addEventListener('mousedown', handleClick)
      return () => document.removeEventListener('mousedown', handleClick)
    }
  }, [open])

  const allSlugs = orgKbs.map((kb) => kb.slug)

  const staleSlugsOnly =
    pref != null &&
    pref.kb_slugs_filter !== null &&
    pref.kb_slugs_filter.length > 0 &&
    pref.kb_slugs_filter.every((s) => !allSlugs.includes(s))
  useEffect(() => {
    if (staleSlugsOnly && !mutation.isPending && !mutation.isError) {
      mutation.mutate({ kb_slugs_filter: null })
    }
  }, [staleSlugsOnly, mutation.isPending, mutation.isError]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!pref || orgKbs.length === 0) return null

  const currentSlugs: string[] =
    pref.kb_slugs_filter === null ? allSlugs : pref.kb_slugs_filter.filter((s) => allSlugs.includes(s))
  const isOn = pref.kb_retrieval_enabled
  const isPending = mutation.isPending

  function toggleRetrieval() {
    mutation.mutate({ kb_retrieval_enabled: !pref!.kb_retrieval_enabled })
  }

  function togglePersonal() {
    mutation.mutate({ kb_personal_enabled: !pref!.kb_personal_enabled })
  }

  function toggleNarrow() {
    mutation.mutate({ kb_narrow: !pref!.kb_narrow })
  }

  function toggleSlug(slug: string) {
    const next = currentSlugs.includes(slug)
      ? currentSlugs.filter((s) => s !== slug)
      : [...currentSlugs, slug]
    const normalized: string[] | null =
      next.length === 0 || next.length === allSlugs.length ? null : next
    mutation.mutate({ kb_slugs_filter: normalized })
  }

  return (
    <div className="flex h-10 shrink-0 items-center border-b border-[var(--color-border)] bg-[var(--color-background)] px-4">
      {/* Invisible overlay — iframe swallows mousedown */}
      {open && (
        <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
      )}

      <div ref={ref} className="relative z-50">
        {/* Single pill */}
        <button
          type="button"
          onClick={() => isOn ? setOpen((v) => !v) : toggleRetrieval()}
          disabled={isPending}
          className={[
            'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors',
            isPending ? 'opacity-50' : '',
            isOn
              ? 'bg-[var(--color-rl-accent)]/10 text-[var(--color-foreground)] hover:bg-[var(--color-rl-accent)]/20'
              : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:bg-[var(--color-secondary)]',
          ].join(' ')}
        >
          <Brain className="h-3.5 w-3.5" />
          {isOn ? (
            <>
              {m.chat_kb_bar_toggle_label()}
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />
              <ChevronDown className="h-3 w-3 opacity-50" />
            </>
          ) : (
            m.chat_kb_bar_toggle_label()
          )}
        </button>

        {/* Dropdown */}
        {open && isOn && (
          <div className="absolute left-0 top-full z-50 mt-1.5 w-56 rounded-xl border border-[var(--color-border)] bg-[var(--color-background)] py-1 shadow-lg">
            {/* Personal KB */}
            <button
              type="button"
              onClick={togglePersonal}
              disabled={isPending}
              className="flex w-full items-center gap-2.5 px-3 py-2 text-xs hover:bg-[var(--color-secondary)] transition-colors text-left"
            >
              <Checkbox checked={pref.kb_personal_enabled} />
              <span className="text-[var(--color-foreground)]">{m.chat_kb_bar_personal_label()}</span>
            </button>

            {/* Org KBs */}
            {orgKbs.map((kb) => (
              <button
                key={kb.slug}
                type="button"
                onClick={() => toggleSlug(kb.slug)}
                disabled={isPending}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-xs hover:bg-[var(--color-secondary)] transition-colors text-left"
              >
                <Checkbox checked={currentSlugs.includes(kb.slug)} />
                <span className="truncate text-[var(--color-foreground)]">{kb.name}</span>
              </button>
            ))}

            {/* Separator */}
            <div className="my-1 border-t border-[var(--color-border)]" />

            {/* Narrow mode */}
            <button
              type="button"
              onClick={toggleNarrow}
              disabled={isPending}
              className="flex w-full items-center gap-2.5 px-3 py-2 text-xs hover:bg-[var(--color-secondary)] transition-colors text-left"
            >
              <Checkbox checked={pref.kb_narrow} />
              <span className="text-[var(--color-foreground)]">{m.chat_kb_bar_narrow_label()}</span>
            </button>

            {/* Separator + disable */}
            <div className="my-1 border-t border-[var(--color-border)]" />
            <button
              type="button"
              onClick={() => { toggleRetrieval(); setOpen(false) }}
              disabled={isPending}
              className="flex w-full items-center gap-2.5 px-3 py-2 text-xs text-[var(--color-destructive)] hover:bg-[var(--color-destructive-bg)] transition-colors text-left"
            >
              {m.chat_kb_bar_tooltip_off()}
            </button>
          </div>
        )}
      </div>

      {/* Status */}
      {mutation.isPending && (
        <span className="ml-auto text-xs text-[var(--color-muted-foreground)]">
          {m.chat_kb_bar_saving()}
        </span>
      )}
      {mutation.isError && (
        <span className="ml-auto text-xs text-[var(--color-destructive)]">
          {m.chat_kb_bar_save_error()}
        </span>
      )}
    </div>
  )
}

function Checkbox({ checked }: { checked: boolean }) {
  return (
    <span className={[
      'flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors',
      checked
        ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]'
        : 'border-[var(--color-border)]',
    ].join(' ')}>
      {checked && (
        <svg className="h-3 w-3 text-white" viewBox="0 0 12 12" fill="none">
          <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </span>
  )
}
