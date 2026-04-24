// TODO: wire up when backend exposes POST /sources (URL) endpoint for app-type KBs
import { Link2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'

interface UrlSourceFormProps {
  kbSlug: string
  onBack: () => void
}

export function UrlSourceForm({ kbSlug: _kbSlug, onBack }: UrlSourceFormProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center space-y-4">
      <Link2 className="h-10 w-10 text-gray-300" />
      <h2 className="text-base font-medium text-gray-900">
        {m.knowledge_add_source_url_label()}
      </h2>
      <p className="text-sm text-gray-400 max-w-xs">
        {m.knowledge_add_source_url_subtitle()}
      </p>
      <p className="text-sm font-medium text-gray-400">
        {m.knowledge_add_source_coming_soon()}
      </p>
      <Button type="button" disabled variant="secondary">
        {m.knowledge_add_source_url_label()}
      </Button>
      <button
        type="button"
        onClick={onBack}
        className="text-sm text-gray-400 hover:text-gray-900 transition-colors"
      >
        {m.knowledge_add_source_back()}
      </button>
    </div>
  )
}
