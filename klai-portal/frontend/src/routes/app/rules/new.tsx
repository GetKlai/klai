import { createFileRoute } from '@tanstack/react-router'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RuleFormPage, EMPTY_RULE_FORM } from './-rule-form'

export const Route = createFileRoute('/app/rules/new')({
  component: () => (
    <ProductGuard product="chat">
      <RuleFormPage mode="new" initialForm={EMPTY_RULE_FORM} />
    </ProductGuard>
  ),
})
