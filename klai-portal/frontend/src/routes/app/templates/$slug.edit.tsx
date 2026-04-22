import { createFileRoute, useParams } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { apiFetch } from '@/lib/apiFetch'
import {
  TemplateFormPage,
  EMPTY_TEMPLATE_FORM,
} from './-template-form'

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

export const Route = createFileRoute('/app/templates/$slug/edit')({
  component: () => (
    <ProductGuard product="chat">
      <EditTemplatePage />
    </ProductGuard>
  ),
})

function EditTemplatePage() {
  const { slug } = useParams({ from: '/app/templates/$slug/edit' })

  const { data, isLoading } = useQuery<Template[]>({
    queryKey: ['app-templates'],
    queryFn: async () => apiFetch<Template[]>('/api/app/templates'),
  })

  if (isLoading) {
    return (
      <div className="mx-auto max-w-lg px-6 py-10">
        <div className="h-14 rounded-lg bg-gray-50 animate-pulse mb-4" />
        <div className="h-14 rounded-lg bg-gray-50 animate-pulse" />
      </div>
    )
  }

  const tpl = (data ?? []).find((t) => t.slug === slug)

  if (!tpl) {
    return (
      <div className="mx-auto max-w-lg px-6 py-10">
        <p className="text-sm text-[var(--color-destructive)]">
          Template niet gevonden.
        </p>
      </div>
    )
  }

  const initialForm = {
    ...EMPTY_TEMPLATE_FORM,
    name: tpl.name,
    description: tpl.description ?? '',
    prompt_text: tpl.prompt_text,
    scope: tpl.scope,
  }

  return <TemplateFormPage mode="edit" initialForm={initialForm} slug={tpl.slug} />
}
