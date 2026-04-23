import { createFileRoute } from '@tanstack/react-router'
import { EMPTY_TEMPLATE_FORM, TemplateFormPage } from '@/routes/app/templates/-template-form'

// Admin-only new template page. Guarded by /admin/route.tsx (requireAdmin: true),
// so the admin-gate on scope="org" is always satisfied here.
export const Route = createFileRoute('/admin/templates/new')({
  component: () => (
    <TemplateFormPage
      mode="new"
      initialForm={EMPTY_TEMPLATE_FORM}
      backPath="/admin/templates"
    />
  ),
})
