import { createFileRoute, useParams } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { apiFetch } from '@/lib/apiFetch'
import { RuleFormPage, type RuleType, EMPTY_RULE_FORM } from './-rule-form'

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

export const Route = createFileRoute('/app/rules/$slug/edit')({
  component: () => (
    <ProductGuard product="chat">
      <EditRulePage />
    </ProductGuard>
  ),
})

function EditRulePage() {
  const { slug } = useParams({ from: '/app/rules/$slug/edit' })

  const { data, isLoading } = useQuery<Rule[]>({
    queryKey: ['app-rules'],
    queryFn: async () => apiFetch<Rule[]>('/api/app/rules'),
  })

  if (isLoading) {
    return (
      <div className="mx-auto max-w-lg px-6 py-10">
        <div className="h-14 rounded-lg bg-gray-50 animate-pulse mb-4" />
        <div className="h-14 rounded-lg bg-gray-50 animate-pulse" />
      </div>
    )
  }

  const rule = (data ?? []).find((r) => r.slug === slug)

  if (!rule) {
    return (
      <div className="mx-auto max-w-lg px-6 py-10">
        <p className="text-sm text-[var(--color-destructive)]">
          Regel niet gevonden.
        </p>
      </div>
    )
  }

  const initialForm = {
    ...EMPTY_RULE_FORM,
    name: rule.name,
    description: rule.description ?? '',
    rule_text: rule.rule_text,
    scope: rule.scope,
    rule_type: rule.rule_type,
  }

  return <RuleFormPage mode="edit" initialForm={initialForm} slug={rule.slug} />
}
