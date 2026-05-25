import { createFileRoute } from '@tanstack/react-router'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { TaxonomyTab } from './_components/TaxonomyTab'
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
          Komt eraan - laatste sync-runs per connector met status en fout-reden.
        </p>
      </section>
    </div>
  )
}
