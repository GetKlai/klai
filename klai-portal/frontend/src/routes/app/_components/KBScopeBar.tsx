import { useEffect, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, ChevronDown } from 'lucide-react'

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

  // Close dropdown when clicking outside
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

  // Auto-heal stale slug filter: if all stored slugs no longer exist in the org,
  // reset to null (= all KBs) rather than silently sending a dead filter to the hook.
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

  // Hide bar when no org KBs are configured (KB feature not provisioned)
  if (!pref || orgKbs.length === 0) return null

  // null means all KBs selected; filter out stale slugs no longer in the org
  const currentSlugs: string[] =
    pref.kb_slugs_filter === null ? allSlugs : pref.kb_slugs_filter.filter((s) => allSlugs.includes(s))
  const selectedCount = currentSlugs.length
  const isOn = pref.kb_retrieval_enabled
  const isPending = mutation.isPending

  const filterLabel =
    pref.kb_slugs_filter === null || selectedCount === orgKbs.length
      ? m.chat_kb_bar_org_filter_placeholder()
      : `${selectedCount} / ${orgKbs.length}`

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
    // Normalize: if all selected or none selected, send null (server treats null as "all")
    const normalized: string[] | null =
      next.length === 0 || next.length === allSlugs.length ? null : next
    mutation.mutate({ kb_slugs_filter: normalized })
  }

  return (
    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-background)] px-4">
      {/* KB retrieval toggle */}
      <button
        type="button"
        onClick={toggleRetrieval}
        disabled={isPending}
        title={isOn ? m.chat_kb_bar_tooltip_on() : m.chat_kb_bar_tooltip_off()}
        className={[
          'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium tracking-wide uppercase transition-colors',
          isPending ? 'opacity-50' : '',
          isOn
            ? 'bg-[var(--color-rl-accent)]/12 text-[var(--color-foreground)]'
            : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
        ].join(' ')}
      >
        <BookOpen className="h-3 w-3" />
        {m.chat_kb_bar_toggle_label()}
        <span
          className={[
            'h-1.5 w-1.5 rounded-full',
            isOn ? 'bg-[var(--color-success)]' : 'bg-[var(--color-muted-foreground)]/40',
          ].join(' ')}
        />
      </button>

      {isOn && (
        <>
          {/* Personal KB pill */}
          <button
            type="button"
            onClick={togglePersonal}
            disabled={isPending}
            title={m.chat_kb_bar_personal_tooltip()}
            className={[
              'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium tracking-wide uppercase transition-colors',
              isPending ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
              pref.kb_personal_enabled
                ? 'bg-[var(--color-rl-accent)]/12 text-[var(--color-foreground)]'
                : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
            ].join(' ')}
          >
            <span className={['h-1.5 w-1.5 rounded-full', pref.kb_personal_enabled ? 'bg-[var(--color-success)]' : 'bg-[var(--color-muted-foreground)]/40'].join(' ')} />
            {m.chat_kb_bar_personal_label()}
          </button>

          {/* Org KB filter dropdown */}
          <div ref={dropdownRef} className="relative">
            <button
              type="button"
              onClick={() => setDropdownOpen((v) => !v)}
              disabled={isPending}
              className={[
                'flex items-center gap-1 rounded-full border border-[var(--color-border)] px-2.5 py-1 text-[11px] font-medium tracking-wide uppercase transition-colors',
                isPending
                  ? 'opacity-50'
                  : 'hover:border-[var(--color-foreground)]/30 hover:text-[var(--color-foreground)]',
                'text-[var(--color-muted-foreground)]',
              ].join(' ')}
            >
              {filterLabel}
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
                    <span className={['h-1.5 w-1.5 rounded-full shrink-0', currentSlugs.includes(kb.slug) ? 'bg-[var(--color-success)]' : 'bg-[var(--color-muted-foreground)]/30'].join(' ')} />
                    <span className="truncate text-[var(--color-foreground)]">{kb.name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Narrow mode pill */}
          <button
            type="button"
            onClick={toggleNarrow}
            disabled={isPending}
            className={[
              'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium tracking-wide uppercase transition-colors',
              isPending ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
              pref.kb_narrow
                ? 'bg-[var(--color-rl-accent)]/12 text-[var(--color-foreground)]'
                : 'text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
            ].join(' ')}
          >
            {m.chat_kb_bar_narrow_label()}
          </button>
        </>
      )}

      {/* Status indicators */}
      {mutation.isPending && (
        <span className="ml-auto text-[11px] text-[var(--color-muted-foreground)]">
          {m.chat_kb_bar_saving()}
        </span>
      )}
      {mutation.isError && (
        <span className="ml-auto text-[11px] text-[var(--color-destructive)]">
          {m.chat_kb_bar_save_error()}
        </span>
      )}
    </div>
  )
}
