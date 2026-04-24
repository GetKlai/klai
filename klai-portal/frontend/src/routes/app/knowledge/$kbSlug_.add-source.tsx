import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ProductGuard } from '@/components/layout/ProductGuard'
import * as m from '@/paraglide/messages'
import { SourceTypeGrid } from './$kbSlug_.add-source._components/SourceTypeGrid'
import { FileUploadForm } from './$kbSlug_.add-source._components/FileUploadForm'
import { UrlSourceForm } from './$kbSlug_.add-source._components/UrlSourceForm'
import { YouTubeSourceForm } from './$kbSlug_.add-source._components/YouTubeSourceForm'
import { TextSourceForm } from './$kbSlug_.add-source._components/TextSourceForm'
import type { UploadType } from './$kbSlug_.add-source._components/source-types'

// -- Types -------------------------------------------------------------------

type AddSourceSearch = { type?: UploadType }

const VALID_UPLOAD_TYPES = new Set<string>(['file', 'url', 'youtube', 'text'])

// -- Route -------------------------------------------------------------------

export const Route = createFileRoute('/app/knowledge/$kbSlug_/add-source')({
  validateSearch: (s: Record<string, unknown>): AddSourceSearch => ({
    type: VALID_UPLOAD_TYPES.has(s.type as string)
      ? (s.type as UploadType)
      : undefined,
  }),
  component: () => (
    <ProductGuard product="knowledge">
      <AddSourcePage />
    </ProductGuard>
  ),
})

// -- Page component ----------------------------------------------------------

function AddSourcePage() {
  const { kbSlug } = Route.useParams()
  const { type: initialType } = Route.useSearch()
  const navigate = useNavigate()

  const [selectedUpload, setSelectedUpload] = useState<UploadType | null>(
    initialType ?? null,
  )

  function goBack() {
    void navigate({
      to: '/app/knowledge/$kbSlug/overview',
      params: { kbSlug },
    })
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Page header */}
      <div className="flex items-center justify-between mb-2">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.knowledge_add_source_title()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={goBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.knowledge_add_source_back()}
        </Button>
      </div>
      <p className="text-sm text-gray-400 mb-6">
        {m.knowledge_add_source_subtitle()}
      </p>

      {/* Picker / inline forms */}
      {selectedUpload === null ? (
        <SourceTypeGrid
          kbSlug={kbSlug}
          onSelectUpload={(t) => setSelectedUpload(t)}
        />
      ) : selectedUpload === 'file' ? (
        <FileUploadForm
          kbSlug={kbSlug}
          onBack={() => setSelectedUpload(null)}
        />
      ) : selectedUpload === 'url' ? (
        <UrlSourceForm
          kbSlug={kbSlug}
          onBack={() => setSelectedUpload(null)}
        />
      ) : selectedUpload === 'youtube' ? (
        <YouTubeSourceForm
          kbSlug={kbSlug}
          onBack={() => setSelectedUpload(null)}
        />
      ) : (
        <TextSourceForm
          kbSlug={kbSlug}
          onBack={() => setSelectedUpload(null)}
        />
      )}
    </div>
  )
}
