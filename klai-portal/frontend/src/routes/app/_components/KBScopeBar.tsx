import { useEffect, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, ChevronDown, X } from 'lucide-react'

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
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

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
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    if (dropdownOpen) {
      document.addEventListener('mousedown', handleClick)
      return () => document.removeEventListener('mousedown', handleClick)
    }
  }, [dropdownOpen])

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
  const selectedCount = currentSlugs.length
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

  // Count active sources
  const activeCount =
    (pref.kb_personal_enabled ? 1 : 0) + selectedCount

  return (
    <div className="flex h-10 shrink-0 items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-background)] px-4">
      {/* Invisible overlay to catch clicks outside the dropdown (iframe swallows mousedown) */}
      {dropdownOpen && (
        <div className="fixed inset-0 z-40" onClick={() => setDropdownOpen(false)} />
      )}

      {/* Main knowledge toggle — clean pill */}
      <button
        type="button"
        onClick={toggleRetrieval}
        disabled={isPending}
        title={isOn ? m.chat_kb_bar_tooltip_on() : m.chat_kb_bar_tooltip_off()}
        className={[
          'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs transition-colors',
          isPending ? 'opacity-50' : '',
          isOn
            ? 'bg-[var(--color-rl-accent)]/10 text-[var(--color-foreground)]'
            : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:bg-[var(--color-secondary)]',
        ].join(' ')}
      >
        <Brain className="h-3.5 w-3.5" />
        <span className="font-medium">
          {isOn ? `${activeCount} ${activeCount === 1 ? 'bron' : 'bronnen'}` : m.chat_kb_bar_toggle_label()}
        </span>
        {isOn && (
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />
        )}
      </button>

      {isOn && (
        <>
          {/* Source pills — clean, lowercase */}
          <div className="flex items-center gap-1.5">
            {/* Personal */}
            <button
              type="button"
              onClick={togglePersonal}
              disabled={isPending}
              title={m.chat_kb_bar_personal_tooltip()}
              className={[
                'flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition-colors',
                isPending ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
                pref.kb_personal_enabled
                  ? 'bg-[var(--color-secondary)] text-[var(--color-foreground)]'
                  : 'text-[var(--color-muted-foreground)] hover:bg-[var(--color-secondary)]',
              ].join(' ')}
            >
              {m.chat_kb_bar_personal_label()}
              {pref.kb_personal_enabled && (
                <X className="h-3 w-3 opacity-40 hover:opacity-100" />
              )}
            </button>

            {/* Org KB selector */}
            <div ref={dropdownRef} className="relative z-50">
              <button
                type="button"
                onClick={() => setDropdownOpen((v) => !v)}
                disabled={isPending}
                className={[
                  'flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition-colors',
                  isPending ? 'opacity-50' : 'cursor-pointer',
                  selectedCount > 0
                    ? 'bg-[var(--color-secondary)] text-[var(--color-foreground)]'
                    : 'text-[var(--color-muted-foreground)] hover:bg-[var(--color-secondary)]',
                ].join(' ')}
              >
                {selectedCount === orgKbs.length
                  ? m.chat_kb_bar_org_filter_placeholder()
                  : `${selectedCount} / ${orgKbs.length}`}
                <ChevronDown className="h-3 w-3" />
              </button>

              {dropdownOpen && (
                <div className="absolute left-0 top-full z-50 mt-1.5 w-52 rounded-xl border border-[var(--color-border)] bg-[var(--color-background)] py-1.5 shadow-lg">
                  {orgKbs.map((kb) => (
                    <button
                      key={kb.slug}
                      type="button"
                      onClick={() => toggleSlug(kb.slug)}
                      disabled={isPending}
                      className="flex w-full cursor-pointer items-center gap-2.5 px-3 py-2 text-xs hover:bg-[var(--color-secondary)] transition-colors text-left"
                    >
                      <span className={[
                        'h-3.5 w-3.5 rounded border flex items-center justify-center',
                        currentSlugs.includes(kb.slug)
                          ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]'
                          : 'border-[var(--color-border)]',
                      ].join(' ')}>
                        {currentSlugs.includes(kb.slug) && (
                          <svg className="h-2.5 w-2.5 text-white" viewBox="0 0 12 12" fill="none">
                            <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        )}
                      </span>
                      <span className="truncate text-[var(--color-foreground)]">{kb.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Narrow mode — subtle toggle */}
            {pref.kb_narrow && (
              <span className="rounded-full bg-[var(--color-rl-accent)]/10 px-2 py-1 text-xs text-[var(--color-foreground)]">
                {m.chat_kb_bar_narrow_label()}
              </span>
            )}
            {!pref.kb_narrow && (
              <button
                type="button"
                onClick={toggleNarrow}
                disabled={isPending}
                className="rounded-full px-2 py-1 text-xs text-[var(--color-muted-foreground)] hover:bg-[var(--color-secondary)] transition-colors"
              >
                {m.chat_kb_bar_narrow_label()}
              </button>
            )}
          </div>
        </>
      )}

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
