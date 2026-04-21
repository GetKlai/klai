import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Shield } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { Badge } from '@/components/ui/badge'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/rules/')({
  component: () => (
    <ProductGuard product="chat">
      <RulesPage />
    </ProductGuard>
  ),
})

type RuleType =
  | 'instruction'
  | 'pii_block'
  | 'pii_redact'
  | 'keyword_block'
  | 'keyword_redact'

interface Rule {
  id: number
  name: string
  slug: string
  description: string | null
  rule_text: string
  scope: string
  rule_type: RuleType
  is_active: boolean
  created_by: string
}

interface RuleFormState {
  name: string
  description: string
  rule_text: string
  scope: string
  rule_type: RuleType
}

const EMPTY_FORM: RuleFormState = {
  name: '',
  description: '',
  rule_text: '',
  scope: 'global',
  rule_type: 'pii_redact',
}

function getRuleTypeLabel(type: RuleType): string {
  switch (type) {
    case 'instruction':
      return m.rules_type_instruction()
    case 'pii_block':
      return m.rules_type_pii_block()
    case 'pii_redact':
      return m.rules_type_pii_redact()
    case 'keyword_block':
      return m.rules_type_keyword_block()
    case 'keyword_redact':
      return m.rules_type_keyword_redact()
  }
}

function getRuleTypeBadgeVariant(
  type: RuleType,
): 'secondary' | 'destructive' | 'warning' {
  switch (type) {
    case 'instruction':
      return 'secondary'
    case 'pii_block':
    case 'keyword_block':
      return 'destructive'
    case 'pii_redact':
    case 'keyword_redact':
      return 'warning'
  }
}

function isPiiType(type: RuleType): boolean {
  return type === 'pii_block' || type === 'pii_redact'
}

function isKeywordType(type: RuleType): boolean {
  return type === 'keyword_block' || type === 'keyword_redact'
}

function RulesPage() {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<RuleFormState>(EMPTY_FORM)
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null)

  const { data, isLoading } = useQuery<Rule[]>({
    queryKey: ['app-rules'],
    queryFn: async () => apiFetch<Rule[]>('/api/app/rules'),
  })

  const createMutation = useMutation({
    mutationFn: async (body: RuleFormState) =>
      apiFetch('/api/app/rules', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-rules'] })
      setShowCreate(false)
      setForm(EMPTY_FORM)
    },
  })

  const updateMutation = useMutation({
    mutationFn: async ({ slug, body }: { slug: string; body: Partial<RuleFormState> }) =>
      apiFetch(`/api/app/rules/${slug}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-rules'] })
      setEditId(null)
      setForm(EMPTY_FORM)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (slug: string) =>
      apiFetch(`/api/app/rules/${slug}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-rules'] })
      setDeleteConfirm(null)
    },
  })

  const rules = data ?? []

  function startEdit(r: Rule) {
    setEditId(r.id)
    setForm({
      name: r.name,
      description: r.description ?? '',
      rule_text: r.rule_text,
      scope: r.scope,
      rule_type: r.rule_type,
    })
    setShowCreate(true)
  }

  function handleSave() {
    const body: RuleFormState = {
      ...form,
      rule_text: isPiiType(form.rule_type) ? '' : form.rule_text,
    }
    if (editId) {
      const r = rules.find((r) => r.id === editId)
      if (r) updateMutation.mutate({ slug: r.slug, body })
    } else {
      createMutation.mutate(body)
    }
  }

  function handleCancel() {
    setShowCreate(false)
    setEditId(null)
    setForm(EMPTY_FORM)
  }

  const isSaving = createMutation.isPending || updateMutation.isPending
  const needsText = !isPiiType(form.rule_type)
  const isSaveDisabled =
    !form.name.trim() || (needsText && !form.rule_text.trim()) || isSaving

  const textFieldLabel = isKeywordType(form.rule_type)
    ? m.rules_field_keywords_label()
    : m.rules_field_text_label()
  const textFieldPlaceholder = isKeywordType(form.rule_type)
    ? m.rules_field_keywords_placeholder()
    : m.rules_field_text_placeholder()

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">{m.rules_page_title()}</h1>
          <p className="mt-1 text-sm text-gray-400">
            {m.rules_page_subtitle()}
          </p>
        </div>
        {!showCreate && (
          <button
            type="button"
            onClick={() => { setEditId(null); setForm(EMPTY_FORM); setShowCreate(true) }}
            className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            {m.rules_new_button()}
          </button>
        )}
      </div>

      {/* Create/Edit form */}
      {showCreate && (
        <div className="mb-8 rounded-lg border border-gray-200 p-6 space-y-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {editId ? m.rules_form_edit_title() : m.rules_form_new_title()}
          </h2>

          <div>
            <label htmlFor="rule-type" className="block text-sm font-medium text-gray-700 mb-1">
              {m.rules_type_label()}
            </label>
            <select
              id="rule-type"
              value={form.rule_type}
              onChange={(e) => setForm({ ...form, rule_type: e.target.value as RuleType })}
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none focus:ring-2 focus:ring-gray-400"
            >
              <option value="pii_redact">{m.rules_type_pii_redact()}</option>
              <option value="pii_block">{m.rules_type_pii_block()}</option>
              <option value="keyword_redact">{m.rules_type_keyword_redact()}</option>
              <option value="keyword_block">{m.rules_type_keyword_block()}</option>
            </select>
          </div>

          <div>
            <label htmlFor="rule-name" className="block text-sm font-medium text-gray-700 mb-1">{m.rules_field_name_label()}</label>
            <input
              id="rule-name"
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={m.rules_field_name_placeholder()}
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400"
            />
          </div>

          <div>
            <label htmlFor="rule-description" className="block text-sm font-medium text-gray-700 mb-1">{m.rules_field_description_label()}</label>
            <input
              id="rule-description"
              type="text"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder={m.rules_field_description_placeholder()}
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400"
            />
          </div>

          {isPiiType(form.rule_type) ? (
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5">
              <p className="text-xs text-gray-600 leading-relaxed">{m.rules_field_pii_hint()}</p>
            </div>
          ) : (
            <div>
              <label htmlFor="rule-text" className="block text-sm font-medium text-gray-700 mb-1">{textFieldLabel}</label>
              <textarea
                id="rule-text"
                value={form.rule_text}
                onChange={(e) => setForm({ ...form, rule_text: e.target.value })}
                placeholder={textFieldPlaceholder}
                rows={5}
                className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400 resize-none"
              />
              <p className="mt-1 text-xs text-gray-400">{form.rule_text.length}/5000</p>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{m.rules_field_scope_label()}</label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setForm({ ...form, scope: 'global' })}
                className={`rounded-lg px-4 py-1.5 text-sm font-medium border transition-colors ${
                  form.scope === 'global'
                    ? 'bg-gray-900 text-white border-gray-900'
                    : 'border-gray-200 text-gray-700 hover:bg-gray-50'
                }`}
              >
                {m.rules_scope_organization()}
              </button>
              <button
                type="button"
                onClick={() => setForm({ ...form, scope: 'personal' })}
                className={`rounded-lg px-4 py-1.5 text-sm font-medium border transition-colors ${
                  form.scope === 'personal'
                    ? 'bg-gray-900 text-white border-gray-900'
                    : 'border-gray-200 text-gray-700 hover:bg-gray-50'
                }`}
              >
                {m.rules_scope_personal()}
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2 pt-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={isSaveDisabled}
              className="rounded-lg bg-gray-900 px-5 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors disabled:opacity-50"
            >
              {isSaving ? m.rules_saving() : editId ? m.rules_update_button() : m.rules_save_button()}
            </button>
            <button
              type="button"
              onClick={handleCancel}
              className="rounded-lg border border-gray-200 px-5 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              {m.rules_cancel_button()}
            </button>
          </div>
        </div>
      )}

      {/* Rule list */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <div key={i} className="h-20 rounded-lg bg-gray-50 animate-pulse" />)}
        </div>
      ) : rules.length === 0 && !showCreate ? (
        <div className="flex flex-col items-center gap-5 rounded-lg border border-gray-200 py-16 px-6">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gray-50">
            <Shield size={24} strokeWidth={1.5} className="text-gray-300" />
          </div>
          <div className="text-center space-y-2 max-w-md">
            <p className="text-base font-medium text-gray-900">{m.rules_empty_title()}</p>
            <p className="text-sm text-gray-400 leading-relaxed">
              {m.rules_empty_description()}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="rounded-lg bg-gray-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            {m.rules_empty_cta()}
          </button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {rules.map((r) => (
            <div key={r.id} className="rounded-lg border border-gray-200 p-5 hover:shadow-sm transition-shadow">
              <div className="flex items-start justify-between mb-2">
                <div className="min-w-0 flex-1">
                  <h3 className="text-sm font-semibold text-gray-900">{r.name}</h3>
                  <div className="mt-1.5">
                    <Badge
                      variant={getRuleTypeBadgeVariant(r.rule_type)}
                      className="text-[10px] uppercase tracking-wide"
                    >
                      {getRuleTypeLabel(r.rule_type)}
                    </Badge>
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={() => startEdit(r)}
                    className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-50 transition-colors"
                  >
                    <Pencil size={14} />
                  </button>
                  {deleteConfirm === r.id ? (
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => deleteMutation.mutate(r.slug)}
                        className="px-2 py-1 text-xs text-[var(--color-destructive)] hover:bg-gray-50 rounded transition-colors"
                      >
                        {m.rules_delete_button()}
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeleteConfirm(null)}
                        className="px-2 py-1 text-xs text-gray-400 hover:bg-gray-50 rounded transition-colors"
                      >
                        {m.rules_delete_cancel()}
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setDeleteConfirm(r.id)}
                      className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-50 transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </div>
              {r.description && (
                <p className="text-xs text-gray-400 mb-2 line-clamp-2">{r.description}</p>
              )}
              {r.rule_text && (
                <p className="text-xs text-gray-400 line-clamp-2 mb-3">{r.rule_text}</p>
              )}
              <div className="flex items-center gap-2">
                <span className="rounded-full bg-gray-50 px-2.5 py-0.5 text-[10px] font-medium text-gray-700">
                  {r.scope === 'global' ? m.rules_badge_organization() : m.rules_badge_personal()}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
