import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ChevronDown } from 'lucide-react'

import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'

// Superdock-style config bar above the LibreChat iframe.
// Collections picker (active KBs) + optional Templates multi-select picker.
// Backed by existing /api/app/knowledge-bases + /api/app/account/kb-preference.
// Templates dropdown hidden when no templates are configured (backend not
// required until SPEC-TEMPLATES-INJECTION-001).

interface KBPref {
  kb_retrieval_enabled: boolean
  kb_personal_enabled: boolean
  kb_slugs_filter: string[] | null
  kb_narrow: boolean
  kb_pref_version: number
  active_template_ids: number[] | null
}

interface OrgKB {
  slug: string
  name: string
}

interface Template {
  id: number
  name: string
  slug: string
  scope: string
}

export function ChatConfigBar() {
  const queryClient = useQueryClient()
  const [collOpen, setCollOpen] = useState(false)
  const [tmplOpen, setTmplOpen] = useState(false)

  const { data: pref } = useQuery<KBPref>({
    queryKey: ['kb-preference'],
    queryFn: async () => apiFetch<KBPref>('/api/app/account/kb-preference'),
  })

  const { data: kbsData } = useQuery<{ knowledge_bases: OrgKB[] }>({
    queryKey: ['all-kbs-for-bar'],
    queryFn: async () => apiFetch<{ knowledge_bases: OrgKB[] }>('/api/app/knowledge-bases'),
  })

  const { data: templatesData } = useQuery<Template[]>({
    queryKey: ['app-templates-for-bar'],
    queryFn: async () => apiFetch<Template[]>('/api/app/templates'),
    retry: false,
  })

  // Exclude every personal-* slug: the caller's own toggles via "Persoonlijk"
  // and other users' personal KBs should never show up in the chat dropdown.
  const allKbs = (kbsData?.knowledge_bases ?? []).filter((kb) => !kb.slug.startsWith('personal-'))
  const allSlugs = allKbs.map((kb) => kb.slug)
  const currentSlugs: string[] = pref
    ? pref.kb_slugs_filter === null
      ? allSlugs
      : pref.kb_slugs_filter.filter((s) => allSlugs.includes(s))
    : allSlugs

  const mutation = useMutation({
    mutationFn: async (patch: Partial<Omit<KBPref, 'kb_pref_version'>>) =>
      apiFetch<KBPref>('/api/app/account/kb-preference', {
        method: 'PATCH',
        body: JSON.stringify(patch),
      }),
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: ['kb-preference'] })
      const prev = queryClient.getQueryData<KBPref>(['kb-preference'])
      if (prev) queryClient.setQueryData<KBPref>(['kb-preference'], { ...prev, ...patch })
      return { prev }
    },
    onSuccess: (data) => queryClient.setQueryData(['kb-preference'], data),
    onError: (_e, _p, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(['kb-preference'], ctx.prev)
    },
  })

  function toggleSlug(slug: string) {
    const next = currentSlugs.includes(slug)
      ? currentSlugs.filter((s) => s !== slug)
      : [...currentSlugs, slug]
    // null = all on, [] = none, anything else = explicit subset.
    // DO NOT collapse empty to null — that would flip "turn last off" into
    // "turn everything back on" and break user intent.
    mutation.mutate({ kb_slugs_filter: next.length === allSlugs.length ? null : next })
  }

  // "anything active" — true when ANY collection is on (personal or any
  // org KB). Drives the dropdown header button:
  //   * if anythingActive → label "Alles uit", click turns ALL off (incl. personal).
  //   * else              → label "Alles aan", click turns ALL on.
  // Previously this was `allActive` (TRUE only when EVERYTHING was on),
  // which meant a partially-on state always showed "Alles aan" and clicking
  // it re-enabled everything — exactly the user complaint.
  const anythingActive =
    (pref?.kb_personal_enabled ?? false) || currentSlugs.length > 0

  function toggleAll() {
    if (anythingActive) {
      mutation.mutate({ kb_slugs_filter: [], kb_personal_enabled: false })
    } else {
      mutation.mutate({ kb_slugs_filter: null, kb_personal_enabled: true })
    }
  }

  if (!pref || allKbs.length === 0) return null

  const activeNames: string[] = []
  if (pref.kb_personal_enabled) activeNames.push('Persoonlijk')
  for (const kb of allKbs) {
    if (currentSlugs.includes(kb.slug)) activeNames.push(kb.name)
  }

  const allTemplates = templatesData ?? []
  const activeTemplateIds: number[] = pref.active_template_ids ?? []
  const activeTemplates = allTemplates.filter((t) => activeTemplateIds.includes(t.id))

  function toggleTemplate(id: number) {
    const next = activeTemplateIds.includes(id)
      ? activeTemplateIds.filter((x) => x !== id)
      : [...activeTemplateIds, id]
    mutation.mutate({ active_template_ids: next.length === 0 ? null : next })
  }

  function clearTemplates() {
    mutation.mutate({ active_template_ids: null })
  }

  return (
    <div className="flex shrink-0 items-center gap-6 bg-[var(--color-sidebar)] border-b border-[var(--color-sidebar-border)] pl-4 pr-4 pt-3 pb-3">
      {collOpen && <div className="fixed inset-0 z-40" onClick={() => setCollOpen(false)} />}
      {tmplOpen && <div className="fixed inset-0 z-40" onClick={() => setTmplOpen(false)} />}

      {/* Chat met: (knowledge collections) */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-[13px] text-gray-400 whitespace-nowrap">Chat met:</span>

        <div className="relative z-50 min-w-0">
          <button
            type="button"
            onClick={() => setCollOpen((v) => !v)}
            className="flex items-center gap-1.5 text-[14px] font-semibold text-gray-700 hover:text-gray-900 transition-colors truncate"
          >
            <span className="truncate">
              {activeNames.length > 0 ? activeNames.join(', ') : m.chatbar_collections_general_ai()}
            </span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-40" />
          </button>

          {collOpen && (
            <div className="absolute left-0 top-full z-50 mt-2 w-64 rounded-lg border border-gray-200 bg-white py-1.5 shadow-lg">
              <div className="flex items-center justify-between px-3 py-1.5">
                <span className="text-[10px] font-semibold tracking-wide text-gray-400">{m.chatbar_collections_label()}</span>
                <button
                  type="button"
                  onClick={toggleAll}
                  className="text-[10px] font-semibold tracking-wide text-gray-500 hover:text-gray-900 transition-colors"
                >
                  {anythingActive ? m.chatbar_collections_all_off() : m.chatbar_collections_all_on()}
                </button>
              </div>
              {/* Personal */}
              <button
                type="button"
                onClick={() => mutation.mutate({ kb_personal_enabled: !pref.kb_personal_enabled })}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] klai-hover text-left"
              >
                <span
                  className={`h-2 w-2 shrink-0 rounded-full ${pref.kb_personal_enabled ? 'bg-green-500' : 'bg-gray-200'}`}
                />
                <span
                  className={pref.kb_personal_enabled ? 'text-gray-900 font-medium' : 'text-gray-400'}
                >
                  Persoonlijk
                </span>
              </button>
              {/* Org KBs */}
              {allKbs.map((kb) => (
                <button
                  key={kb.slug}
                  type="button"
                  onClick={() => toggleSlug(kb.slug)}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] klai-hover text-left"
                >
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${currentSlugs.includes(kb.slug) ? 'bg-green-500' : 'bg-gray-200'}`}
                  />
                  <span
                    className={
                      currentSlugs.includes(kb.slug)
                        ? 'text-gray-900 font-medium'
                        : 'text-gray-400'
                    }
                  >
                    {kb.name}
                  </span>
                </button>
              ))}

              <div className="mt-1 border-t border-gray-100 pt-1">
                <Link
                  to="/app/knowledge"
                  onClick={() => setCollOpen(false)}
                  className="block px-3 py-2 text-[12px] text-[var(--color-rl-accent-dark)] hover:underline"
                >
                  {m.chatbar_collections_manage()}
                </Link>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Modus: inline segmented two-pill toggle. Replaces the previous
          single-dropdown picker — a wider, single-click affordance the
          user can compare side-by-side instead of opening a menu. The
          selected pill carries the dark fill (matches the page-header
          primary-button pattern); the other shows muted text. Backed by
          the same kb_narrow boolean — the LiteLLM hook
          (deploy/litellm/klai_knowledge.py) reads it per-request and
          injects the corresponding system-prompt header. */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-[13px] text-gray-400 whitespace-nowrap">
          {m.chatbar_mode_label()}:
        </span>
        <div
          role="radiogroup"
          aria-label={m.chatbar_mode_label()}
          className="inline-flex items-center gap-0.5 rounded-full border border-gray-200 p-0.5"
        >
          <button
            type="button"
            onClick={() => { if (pref.kb_narrow) mutation.mutate({ kb_narrow: false }) }}
            role="radio"
            aria-checked={!pref.kb_narrow}
            title={m.chatbar_mode_broad_description()}
            className={
              !pref.kb_narrow
                ? 'rounded-full bg-gray-900 px-3 py-1 text-[12px] font-medium text-white transition-colors'
                : 'rounded-full px-3 py-1 text-[12px] text-gray-500 hover:text-gray-900 klai-hover'
            }
          >
            {m.chatbar_mode_narrow_off()}
          </button>
          <button
            type="button"
            onClick={() => { if (!pref.kb_narrow) mutation.mutate({ kb_narrow: true }) }}
            role="radio"
            aria-checked={pref.kb_narrow}
            title={m.chatbar_mode_narrow_description()}
            className={
              pref.kb_narrow
                ? 'rounded-full bg-gray-900 px-3 py-1 text-[12px] font-medium text-white transition-colors'
                : 'rounded-full px-3 py-1 text-[12px] text-gray-500 hover:text-gray-900 klai-hover'
            }
          >
            {m.chatbar_mode_narrow_on()}
          </button>
        </div>
      </div>

      {/* Templates: multi-select. Hidden when backend has no templates yet. */}
      {allTemplates.length > 0 && (
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[13px] text-gray-400 whitespace-nowrap">
            {m.chatbar_templates_label()}:
          </span>

          <div className="relative z-50 min-w-0">
            <button
              type="button"
              onClick={() => setTmplOpen((v) => !v)}
              className="flex items-center gap-1.5 text-[14px] font-semibold text-gray-700 hover:text-gray-900 transition-colors truncate"
            >
              <span className="truncate">
                {activeTemplates.length > 0
                  ? activeTemplates.map((t) => t.name).join(', ')
                  : m.chatbar_templates_empty()}
              </span>
              <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-40" />
            </button>

            {tmplOpen && (
              <div className="absolute left-0 top-full z-50 mt-2 w-64 rounded-lg border border-gray-200 bg-white py-1.5 shadow-lg">
                <div className="flex items-center justify-between px-3 py-1.5">
                  <span className="text-[10px] font-semibold tracking-wide text-gray-400">
                    {m.chatbar_templates_label()}
                  </span>
                  <button
                    type="button"
                    onClick={clearTemplates}
                    className="text-[10px] font-semibold tracking-wide text-gray-500 hover:text-gray-900 transition-colors"
                  >
                    {m.chatbar_templates_clear()}
                  </button>
                </div>
                {allTemplates.map((t) => {
                  const active = activeTemplateIds.includes(t.id)
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => toggleTemplate(t.id)}
                      className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] klai-hover text-left"
                    >
                      <span
                        className={`h-2 w-2 shrink-0 rounded-full ${active ? 'bg-green-500' : 'bg-gray-200'}`}
                      />
                      <span className={active ? 'text-gray-900 font-medium' : 'text-gray-400'}>
                        {t.name}
                      </span>
                    </button>
                  )
                })}
                <div className="mt-1 border-t border-gray-100 pt-1">
                  <Link
                    to="/app/templates"
                    onClick={() => setTmplOpen(false)}
                    className="block px-3 py-2 text-[12px] text-[var(--color-rl-accent-dark)] hover:underline"
                  >
                    {m.chatbar_templates_manage()}
                  </Link>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
