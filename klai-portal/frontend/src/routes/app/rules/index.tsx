import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Shield } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/rules/')({
  component: () => (
    <ProductGuard product="chat">
      <RulesPage />
    </ProductGuard>
  ),
})

interface Rule {
  id: number
  name: string
  slug: string
  description: string | null
  rule_text: string
  scope: string
  is_active: boolean
  created_by: string
}

function RulesPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState({ name: '', description: '', rule_text: '', scope: 'global' })
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null)

  const { data, isLoading } = useQuery<Rule[]>({
    queryKey: ['app-rules'],
    queryFn: async () => apiFetch<Rule[]>('/api/app/rules', token),
    enabled: !!token,
  })

  const createMutation = useMutation({
    mutationFn: async (body: typeof form) =>
      apiFetch('/api/app/rules', token, { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-rules'] })
      setShowCreate(false)
      setForm({ name: '', description: '', rule_text: '', scope: 'global' })
    },
  })

  const updateMutation = useMutation({
    mutationFn: async ({ slug, body }: { slug: string; body: Partial<typeof form> }) =>
      apiFetch(`/api/app/rules/${slug}`, token, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-rules'] })
      setEditId(null)
      setForm({ name: '', description: '', rule_text: '', scope: 'global' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (slug: string) =>
      apiFetch(`/api/app/rules/${slug}`, token, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-rules'] })
      setDeleteConfirm(null)
    },
  })

  const rules = data ?? []

  function startEdit(r: Rule) {
    setEditId(r.id)
    setForm({ name: r.name, description: r.description ?? '', rule_text: r.rule_text, scope: r.scope })
    setShowCreate(true)
  }

  function handleSave() {
    if (editId) {
      const r = rules.find((r) => r.id === editId)
      if (r) updateMutation.mutate({ slug: r.slug, body: form })
    } else {
      createMutation.mutate(form)
    }
  }

  function handleCancel() {
    setShowCreate(false)
    setEditId(null)
    setForm({ name: '', description: '', rule_text: '', scope: 'global' })
  }

  const isSaving = createMutation.isPending || updateMutation.isPending

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
            onClick={() => { setEditId(null); setForm({ name: '', description: '', rule_text: '', scope: 'global' }); setShowCreate(true) }}
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
            <label className="block text-sm font-medium text-gray-700 mb-1">{m.rules_field_name_label()}</label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={m.rules_field_name_placeholder()}
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{m.rules_field_description_label()}</label>
            <input
              type="text"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder={m.rules_field_description_placeholder()}
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{m.rules_field_text_label()}</label>
            <textarea
              value={form.rule_text}
              onChange={(e) => setForm({ ...form, rule_text: e.target.value })}
              placeholder={m.rules_field_text_placeholder()}
              rows={5}
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400 resize-none"
            />
            <p className="mt-1 text-xs text-gray-400">{form.rule_text.length}/5000</p>
          </div>

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
              disabled={!form.name.trim() || !form.rule_text.trim() || isSaving}
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
                <h3 className="text-sm font-semibold text-gray-900">{r.name}</h3>
                <div className="flex items-center gap-1">
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
              <p className="text-xs text-gray-400 line-clamp-2 mb-3">{r.rule_text}</p>
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
