import { createFileRoute } from '@tanstack/react-router'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { TaxonomyTab } from './_components/TaxonomyTab'

// SPEC-PORTAL-TAXONOMY-EXTRACT-001 reduced this route file to a pure
// route shell. The TaxonomyTab function (and its private helpers
// `CoverageWidget`, `TagCloud`, plus the `MAX_HEALTHY_NODE_COUNT`
// constant) live in `./_components/TaxonomyTab.tsx`. The interior
// split of TaxonomyTab into focused sub-components is tracked under
// SPEC-PORTAL-TAXONOMY-SPLIT-001.

export const Route = createFileRoute('/app/knowledge/$kbSlug/taxonomy')({
  component: () => (
    <RoleGuard minRole="kb_manager">
      <TaxonomyTab />
    </RoleGuard>
  ),
})
