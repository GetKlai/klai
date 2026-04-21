import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Sliders } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/templates/')({
  component: () => (
    <ProductGuard product="chat">
      <TemplatesPage />
    </ProductGuard>
  ),
})

interface Template {
  id: number
  name: string
  slug: string
  description: string | null
  prompt_text: string
  scope: string
  is_active: boolean
  created_by: string
}

function TemplatesPage() {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState({ name: '', description: '', prompt_text: '', scope: 'global' })
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null)

  const { data, isLoading } = useQuery<Template[]>({
    queryKey: ['app-templates'],
    queryFn: async () => apiFetch<Template[]>('/api/app/templates'),
      })

  const createMutation = useMutation({
    mutationFn: async (body: typeof form) =>
      apiFetch('/api/app/templates', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-templates'] })
      setShowCreate(false)
      setForm({ name: '', description: '', prompt_text: '', scope: 'global' })
    },
  })

  const updateMutation = useMutation({
    mutationFn: async ({ slug, body }: { slug: string; body: Partial<typeof form> }) =>
      apiFetch(`/api/app/templates/${slug}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-templates'] })
      setEditId(null)
      setForm({ name: '', description: '', prompt_text: '', scope: 'global' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (slug: string) =>
      apiFetch(`/api/app/templates/${slug}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-templates'] })
      setDeleteConfirm(null)
    },
  })

  const templates = data ?? []

  function startEdit(t: Template) {
    setEditId(t.id)
    setForm({ name: t.name, description: t.description ?? '', prompt_text: t.prompt_text, scope: t.scope })
    setShowCreate(true)
  }

  function handleSave() {
    if (editId) {
      const t = templates.find((t) => t.id === editId)
      if (t) updateMutation.mutate({ slug: t.slug, body: form })
    } else {
      createMutation.mutate(form)
    }
  }

  function handleCancel() {
    setShowCreate(false)
    setEditId(null)
    setForm({ name: '', description: '', prompt_text: '', scope: 'global' })
  }

  const isSaving = createMutation.isPending || updateMutation.isPending

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Templates</h1>
          <p className="mt-1 text-sm text-gray-400">
            Stel tone of voice, instructies en structuur in voor je AI-gesprekken
          </p>
        </div>
        {!showCreate && (
          <button
            type="button"
            onClick={() => { setEditId(null); setForm({ name: '', description: '', prompt_text: '', scope: 'global' }); setShowCreate(true) }}
            className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            Nieuw template
          </button>
        )}
      </div>

      {/* Create/Edit form */}
      {showCreate && (
        <div className="mb-8 rounded-lg border border-gray-200 p-6 space-y-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {editId ? 'Template bewerken' : 'Nieuw template'}
          </h2>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Naam</label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Bijv. Klantenservice, Marketing, Formeel"
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Beschrijving (optioneel)</label>
            <input
              type="text"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="Waar is dit template voor?"
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Instructie</label>
            <textarea
              value={form.prompt_text}
              onChange={(e) => setForm({ ...form, prompt_text: e.target.value })}
              placeholder="Bijv. Antwoord altijd in het Nederlands. Gebruik een vriendelijke en professionele toon. Houd antwoorden kort en bondig."
              rows={5}
              className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:ring-2 focus:ring-gray-400 resize-none"
            />
            <p className="mt-1 text-xs text-gray-400">{form.prompt_text.length}/5000</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Delen met</label>
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
                Organisatie
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
                Alleen ik
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2 pt-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={!form.name.trim() || !form.prompt_text.trim() || isSaving}
              className="rounded-lg bg-gray-900 px-5 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors disabled:opacity-50"
            >
              {isSaving ? 'Opslaan...' : editId ? 'Bijwerken' : 'Aanmaken'}
            </button>
            <button
              type="button"
              onClick={handleCancel}
              className="rounded-lg border border-gray-200 px-5 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              Annuleren
            </button>
          </div>
        </div>
      )}

      {/* Template list */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <div key={i} className="h-20 rounded-lg bg-gray-50 animate-pulse" />)}
        </div>
      ) : templates.length === 0 && !showCreate ? (
        <div className="flex flex-col items-center gap-5 rounded-lg border border-gray-200 py-16 px-6">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gray-50">
            <Sliders size={24} strokeWidth={1.5} className="text-gray-300" />
          </div>
          <div className="text-center space-y-2 max-w-md">
            <p className="text-base font-medium text-gray-900">Nog geen templates</p>
            <p className="text-sm text-gray-400 leading-relaxed">
              Templates bepalen hoe de AI reageert. Stel de toon, taal en stijl in
              en deel ze met je team.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="rounded-lg bg-gray-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            Maak je eerste template
          </button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {templates.map((t) => (
            <div key={t.id} className="rounded-lg border border-gray-200 p-5 hover:shadow-sm transition-shadow">
              <div className="flex items-start justify-between mb-2">
                <h3 className="text-sm font-semibold text-gray-900">{t.name}</h3>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => startEdit(t)}
                    className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-50 transition-colors"
                  >
                    <Pencil size={14} />
                  </button>
                  {deleteConfirm === t.id ? (
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => deleteMutation.mutate(t.slug)}
                        className="px-2 py-1 text-xs text-[var(--color-destructive)] hover:bg-gray-50 rounded transition-colors"
                      >
                        Verwijder
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeleteConfirm(null)}
                        className="px-2 py-1 text-xs text-gray-400 hover:bg-gray-50 rounded transition-colors"
                      >
                        Annuleer
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setDeleteConfirm(t.id)}
                      className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-50 transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </div>
              {t.description && (
                <p className="text-xs text-gray-400 mb-2 line-clamp-2">{t.description}</p>
              )}
              <p className="text-xs text-gray-400 line-clamp-2 mb-3">{t.prompt_text}</p>
              <div className="flex items-center gap-2">
                <span className="rounded-full bg-gray-50 px-2.5 py-0.5 text-[10px] font-medium text-gray-700">
                  {t.scope === 'global' ? 'Organisatie' : 'Persoonlijk'}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
