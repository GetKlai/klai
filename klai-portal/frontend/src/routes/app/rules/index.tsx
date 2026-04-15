import { createFileRoute } from '@tanstack/react-router'
import { Sliders, MessageSquare, BookOpen, Shield } from 'lucide-react'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/rules/')({
  component: () => (
    <ProductGuard product="chat">
      <RulesPage />
    </ProductGuard>
  ),
})

function RulesPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-10" style={{ fontFamily: 'Inter, system-ui, sans-serif' }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Regels</h1>
          <p className="mt-1 text-sm text-gray-400">
            Stel instructies in die de AI altijd volgt
          </p>
        </div>
        <button
          type="button"
          disabled
          className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-300 cursor-not-allowed"
        >
          Nieuwe regel
        </button>
      </div>

      {/* Empty state */}
      <div className="flex flex-col items-center gap-5 rounded-lg border border-gray-200 py-16 px-6">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gray-50">
          <Sliders size={24} strokeWidth={1.5} className="text-gray-300" />
        </div>
        <div className="text-center space-y-2 max-w-md">
          <p className="text-base font-medium text-gray-900">
            Nog geen regels
          </p>
          <p className="text-sm text-gray-400 leading-relaxed">
            Maak regels aan die bepalen hoe de AI reageert.
            Bijvoorbeeld: "Antwoord altijd in het Nederlands" of
            "Gebruik een formele toon". Regels gelden voor al je gesprekken.
          </p>
        </div>
        <button
          type="button"
          disabled
          className="rounded-lg border border-gray-200 px-5 py-2.5 text-sm text-gray-300 cursor-not-allowed"
        >
          Binnenkort beschikbaar
        </button>
      </div>

      {/* Explanation */}
      <div className="mt-8">
        <h2 className="mb-4 text-xs font-medium text-gray-400 uppercase tracking-wider">
          Hoe werken regels?
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="flex flex-col gap-2.5 rounded-lg border border-gray-200 p-5">
            <MessageSquare size={20} strokeWidth={1.5} className="text-gray-300" />
            <p className="text-sm font-medium text-gray-900">In je gesprekken</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              Regels worden automatisch toegepast op elk gesprek dat je voert.
            </p>
          </div>
          <div className="flex flex-col gap-2.5 rounded-lg border border-gray-200 p-5">
            <BookOpen size={20} strokeWidth={1.5} className="text-gray-300" />
            <p className="text-sm font-medium text-gray-900">Per collectie</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              Je kunt regels koppelen aan specifieke kenniscollecties.
            </p>
          </div>
          <div className="flex flex-col gap-2.5 rounded-lg border border-gray-200 p-5">
            <Shield size={20} strokeWidth={1.5} className="text-gray-300" />
            <p className="text-sm font-medium text-gray-900">Altijd veilig</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              EU AI Act compliance wordt automatisch toegepast.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
