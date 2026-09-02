import { CheckCircle2 } from 'lucide-react'
import { Alert } from '@/components/ui/alert'

export type CrawlerAuthStatusState =
  | { kind: 'public'; onChange: () => void }
  | { kind: 'authenticated'; credentialKind: 'cookies' | 'saved'; onChange: () => void }

export function CrawlerAuthStatus({ status }: { status: CrawlerAuthStatusState | null }) {
  if (status === null) return null

  if (status.kind === 'public') {
    return (
      <div className="flex items-center justify-between rounded-lg border border-gray-200 px-4 py-3">
        <div className="flex items-center gap-2 text-xs text-gray-600">
          <CheckCircle2 className="h-3.5 w-3.5 text-[var(--color-success-text)]" />
          Public site - no login needed
        </div>
        <button
          type="button"
          className="text-xs text-gray-600 hover:text-gray-900"
          onClick={status.onChange}
        >
          Actually, it needs login
        </button>
      </div>
    )
  }

  return (
    <Alert variant="success" size="sm">
      <div className="flex items-center justify-between">
        <span>
          {status.credentialKind === 'saved'
            ? 'Logged in - saved authentication verified'
            : 'Logged in - cookies verified'}
        </span>
        <button
          type="button"
          className="text-xs text-gray-600 hover:text-gray-900"
          onClick={status.onChange}
        >
          {status.credentialKind === 'saved' ? 'Change authentication' : 'Edit cookies'}
        </button>
      </div>
    </Alert>
  )
}
