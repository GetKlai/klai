import { createFileRoute, useParams } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { TemplateFormPage, type TemplateFormState, type TemplateScope } from './-template-form'

export const Route = createFileRoute('/app/templates/$slug/edit')({
  component: () => (
    <ProductGuard product="chat">
      <EditTemplateRoute />
    </ProductGuard>
  ),
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
  // Backend enforces scope IN ('org','personal'); narrow defensively in case
  // older data ever ends up in the response.
  const scope: TemplateScope = t.scope === 'personal' ? 'personal' : 'org'
  return {
    name: t.name,
    description: t.description ?? '',
    prompt_text: t.prompt_text,
    scope,
  }
}

function EditTemplateRoute() {
  const { slug } = useParams({ from: '/app/templates/$slug/edit' })

  const { data, isLoading, isError, error, refetch } = useQuery<TemplateResponse>({
    queryKey: ['app-template', slug],
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

  return <TemplateFormPage mode="edit" slug={data.slug} initialForm={toFormState(data)} />
}
