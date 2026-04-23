import { createFileRoute, useParams } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { TemplateFormPage, type TemplateFormState, type TemplateScope } from '@/routes/app/templates/-template-form'

export const Route = createFileRoute('/admin/templates/$slug/edit')({
  component: EditAdminTemplateRoute,
})

interface TemplateResponse {
  id: number
  name: string
  slug: string
  description: string | null
  prompt_text: string
  scope: string
  created_by: string
  is_active: boolean
  created_at: string
  updated_at: string
}

function toFormState(t: TemplateResponse): TemplateFormState {
  const scope: TemplateScope = t.scope === 'personal' ? 'personal' : 'org'
  return {
    name: t.name,
    description: t.description ?? '',
    prompt_text: t.prompt_text,
    scope,
  }
}

function EditAdminTemplateRoute() {
  const { slug } = useParams({ from: '/admin/templates/$slug/edit' })

  const { data, isLoading, isError, error, refetch } = useQuery<TemplateResponse>({
    queryKey: ['admin-template', slug],
    queryFn: async () => apiFetch<TemplateResponse>(`/api/app/templates/${slug}`),
  })

  if (isLoading) {
    return (
      <div className="mx-auto max-w-lg px-6 py-10">
        <p className="text-sm text-gray-400">{m.templates_form_loading()}</p>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="mx-auto max-w-lg px-6 py-10">
        <QueryErrorState error={error ?? new Error('Unknown error')} onRetry={() => void refetch()} />
      </div>
    )
  }

  return (
    <TemplateFormPage
      mode="edit"
      slug={data.slug}
      initialForm={toFormState(data)}
      backPath="/admin/templates"
    />
  )
}
