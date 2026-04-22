import { createFileRoute } from '@tanstack/react-router'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { TemplateFormPage, EMPTY_TEMPLATE_FORM } from './-template-form'

export const Route = createFileRoute('/app/templates/new')({
  component: () => (
    <ProductGuard product="chat">
      <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
    </ProductGuard>
  ),
})
