import { createFileRoute } from '@tanstack/react-router'
import { RoleGuard } from '@/components/layout/RoleGuard'
// TODO: F-table row 1 of SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 § Follow-ups
// will extract TaxonomyTab (currently a 720-line god-component inside
// ./taxonomy.tsx) to _components/TaxonomyTab.tsx. Splitting that monolith
// deserves its own SPEC. Until then, this single cross-route import is
// the deferred-fix marker.
// eslint-disable-next-line klai/no-cross-route-import
import { TaxonomyTab } from './taxonomy'
import { KBOverviewSections } from './_components/KBOverviewSections'

export const Route = createFileRoute('/app/knowledge/$kbSlug/insights')({
  component: () => (
    <RoleGuard minRole="kb_manager">
      <InsightsTab />
    </RoleGuard>
  ),
})

function InsightsTab() {
  const { kbSlug } = Route.useParams()
  return (
    <div className="space-y-8">
      {/* Docs + Statistieken (moved from the retired /overview route). */}
      <KBOverviewSections kbSlug={kbSlug} />

      <section className="border-t border-gray-200 pt-8">
        <TaxonomyTab kbSlug={kbSlug} />
      </section>

      <section className="border-t border-gray-200 pt-8">
        <h2 className="text-sm font-semibold text-gray-900 mb-3">Sync-historie</h2>
        <p className="text-sm text-gray-400">
          Komt eraan — laatste sync-runs per connector met status en fout-reden.
        </p>
      </section>
    </div>
  )
}
