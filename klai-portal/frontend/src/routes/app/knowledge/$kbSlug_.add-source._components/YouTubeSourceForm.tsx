/**
 * YouTube source tile — currently disabled.
 *
 * YouTube blocks all requests from datacenter IPs (including Klai's core-01
 * cluster) to prevent automated scraping. A privacy-friendly EU residential
 * proxy is the planned unblock — we chose NOT to use a shared third-party
 * proxy because user URLs would pass through a company that isn't GDPR-bound
 * to Klai tenants. This component renders the explanation + two
 * alternatives that Klai ALREADY supports today (URL tile, Scribe).
 *
 * The backend route (``POST /api/app/knowledge-bases/{kb}/sources/youtube``)
 * is intentionally kept live — when the proxy lands in SOPS, we flip the
 * tile's ``available`` flag in ``source-types.ts`` and this component gets
 * reverted to a form. No backend changes needed at that point.
 */
import { Link } from '@tanstack/react-router'
import { FileText, Mic, TriangleAlert } from 'lucide-react'
import { SiYoutube } from '@icons-pack/react-simple-icons'
import * as m from '@/paraglide/messages'

interface YouTubeSourceFormProps {
  kbSlug: string
  onBack: () => void
}

export function YouTubeSourceForm({ kbSlug, onBack }: YouTubeSourceFormProps) {
  return (
    <div className="space-y-6">
      {/* Status banner */}
      <div className="flex items-start gap-3 rounded-xl border border-gray-200 bg-black/[0.03] p-4">
        <TriangleAlert className="h-5 w-5 shrink-0 text-[var(--color-warning)] mt-0.5" />
        <div className="space-y-2">
          <p className="text-sm font-medium text-gray-900">
            <SiYoutube className="inline-block h-4 w-4 mr-1.5 -mt-0.5" />
            {m.knowledge_add_source_youtube_disabled_title()}
          </p>
          <p className="text-sm text-gray-400 leading-relaxed">
            {m.knowledge_add_source_youtube_disabled_body()}
          </p>
        </div>
      </div>

      {/* Alternatives */}
      <div>
        <h3 className="text-xs font-medium text-gray-400 mb-3 tracking-wide">
          {m.knowledge_add_source_youtube_disabled_alternatives_heading()}
        </h3>
        <div className="space-y-2">
          <button
            type="button"
            onClick={onBack}
            className="flex w-full items-start gap-3 rounded-xl border border-gray-200 bg-white p-4 text-left transition-colors hover:border-gray-300"
          >
            <FileText className="h-4 w-4 shrink-0 text-[var(--color-accent)] mt-0.5" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-gray-900">
                {m.knowledge_add_source_youtube_disabled_alt_url_title()}
              </p>
              <p className="text-xs text-gray-400">
                {m.knowledge_add_source_youtube_disabled_alt_url_body()}
              </p>
            </div>
          </button>

          <Link
            to="/app/transcribe"
            className="flex items-start gap-3 rounded-xl border border-gray-200 bg-white p-4 text-left transition-colors hover:border-gray-300"
          >
            <Mic className="h-4 w-4 shrink-0 text-[var(--color-accent)] mt-0.5" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-gray-900">
                {m.knowledge_add_source_youtube_disabled_alt_scribe_title()}
              </p>
              <p className="text-xs text-gray-400">
                {m.knowledge_add_source_youtube_disabled_alt_scribe_body()}
              </p>
            </div>
          </Link>
        </div>
      </div>

      {/* Back */}
      <div className="flex items-center gap-3 pt-2">
        <button
          type="button"
          onClick={onBack}
          className="text-sm text-gray-400 hover:text-gray-900 transition-colors"
        >
          {m.knowledge_add_source_back()}
        </button>
        {/* Referenced so the lint rule leaves it alone — kbSlug is part of the
            component contract for when the feature comes back. */}
        <span className="sr-only">{kbSlug}</span>
      </div>
    </div>
  )
}
