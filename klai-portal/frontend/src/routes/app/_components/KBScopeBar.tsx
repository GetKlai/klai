import { useEffect, useRef, useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, ChevronUp } from 'lucide-react'

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
    queryKey: ['all-kbs-for-bar'],
    queryFn: async () => apiFetch<{ knowledge_bases: OrgKB[] }>('/api/app/knowledge-bases', token),
    enabled: !!token,
  })

  // All non-personal KBs (org + custom) — personal KB has its own toggle
  const myUserId = auth.user?.profile?.sub
  const orgKbs = (kbsData?.knowledge_bases ?? []).filter(
    (kb) => kb.slug !== `personal-${myUserId}`,
  )

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
    <div ref={ref} className="relative">
      {/* Pill trigger */}
      <button
        type="button"
        onClick={() => isOn ? setOpen((v) => !v) : toggleRetrieval()}
        disabled={isPending}
        className={[
          'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[11px] font-medium shadow-md border transition-all',
          isPending ? 'opacity-50' : '',
          isOn
            ? 'bg-white border-gray-200 text-gray-900 hover:shadow-lg'
            : 'bg-white/80 border-gray-200 text-gray-400 hover:text-gray-900',
        ].join(' ')}
      >
        <Brain className="h-3.5 w-3.5" />
        {m.chat_kb_bar_toggle_label()}
        {isOn && <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />}
        <ChevronUp className={`h-3 w-3 opacity-40 transition-transform ${open ? '' : 'rotate-180'}`} />
      </button>

      {/* Dropdown — opens upward, shows each collection by name */}
      {open && isOn && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 z-50 mb-2 w-60 rounded-lg border border-gray-200 bg-white py-1.5 shadow-xl">
          <div className="px-3 py-1.5 text-[10px] font-medium text-gray-400 uppercase tracking-wider">
            Collecties
          </div>

          {/* Personal */}
          <button
            type="button"
            onClick={togglePersonal}
            disabled={isPending}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-xs hover:bg-gray-50 transition-colors text-left"
          >
            <Checkbox checked={pref.kb_personal_enabled} />
            <span className="text-gray-900">{m.chat_kb_bar_personal_label()}</span>
          </button>

          {/* Each org KB by name */}
          {orgKbs.map((kb) => (
            <button
              key={kb.slug}
              type="button"
              onClick={() => toggleSlug(kb.slug)}
              disabled={isPending}
              className="flex w-full items-center gap-2.5 px-3 py-2 text-xs hover:bg-gray-50 transition-colors text-left"
            >
              <Checkbox checked={currentSlugs.includes(kb.slug)} />
              <span className="truncate text-gray-900">{kb.name}</span>
            </button>
          ))}

          <div className="my-1 border-t border-gray-200" />

          {/* Narrow mode */}
          <button
            type="button"
            onClick={toggleNarrow}
            disabled={isPending}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-xs hover:bg-gray-50 transition-colors text-left"
          >
            <Checkbox checked={pref.kb_narrow} />
            <span className="text-gray-400">{m.chat_kb_bar_narrow_label()}</span>
          </button>

          <div className="my-1 border-t border-gray-200" />

          <button
            type="button"
            onClick={() => { toggleRetrieval(); setOpen(false) }}
            disabled={isPending}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-xs text-gray-400 hover:bg-gray-50 transition-colors text-left"
          >
            {m.chat_kb_bar_tooltip_off()}
          </button>
        </div>
      )}
    </div>
  )
}

function Checkbox({ checked }: { checked: boolean }) {
  return (
    <span className={[
      'flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border transition-colors',
      checked
        ? 'border-gray-900 bg-gray-900'
        : 'border-gray-200',
    ].join(' ')}>
      {checked && (
        <svg className="h-2.5 w-2.5 text-white" viewBox="0 0 12 12" fill="none">
          <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </span>
  )
}
