import { KeyRound, Loader2 } from 'lucide-react'
import { CookieRowsInput } from '@/components/knowledge/CookieRowsInput'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { CookieRow } from '../$kbSlug/-kb-types'
import { AuthProbeFeedback } from '../-connector-feedback'
import type { AuthGuardSuggestion, AuthProbeResult } from '../-connector-types'
import { buildCrawlerCookies, type CrawlerAuthPayload } from './api'

type CookieSetupMode = {
  kind: 'cookies'
  idPrefix: string
  rows: CookieRow[]
  onChange: (rows: CookieRow[]) => void
  savedCredentials?: {
    hasPrefilledNames: boolean
    onUseSaved: () => void
  }
}

type SavedSetupMode = {
  kind: 'saved'
  onReplace: () => void
  onUseWithoutLogin: () => void
}

type CrawlerAuthSetupStepProps = {
  baseUrl: string
  mode: CookieSetupMode | SavedSetupMode
  isPending: boolean
  error: string | null
  result: AuthProbeResult | null
  onProbe: (payload: CrawlerAuthPayload) => void
  onNext: (authGuard: AuthGuardSuggestion | null) => void
  onBack: () => void
}

export function CrawlerAuthSetupStep({
  baseUrl,
  mode,
  isPending,
  error,
  result,
  onProbe,
  onNext,
  onBack,
}: CrawlerAuthSetupStepProps) {
  const hasSavedCredentials = mode.kind === 'saved' || mode.savedCredentials !== undefined

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 p-4 space-y-3">
        {hasSavedCredentials ? (
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-gray-900">Authentication cookies</p>
              <p className="text-xs text-gray-600">Saved cookies are encrypted and stay hidden.</p>
            </div>
            {mode.kind === 'cookies' && mode.savedCredentials && (
              <Button type="button" size="sm" variant="outline" onClick={mode.savedCredentials.onUseSaved}>
                Use saved
              </Button>
            )}
          </div>
        ) : (
          <p className="text-sm font-medium text-gray-900">Authentication cookies</p>
        )}

        {mode.kind === 'saved' ? (
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-3 space-y-3">
            <div className="flex items-center gap-2 text-sm text-gray-900">
              <KeyRound className="h-4 w-4 text-gray-500" />
              Saved authentication configured
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={isPending || !baseUrl}
                onClick={() => onProbe({ use_saved_credentials: true })}
              >
                {isPending ? (
                  <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />Testing...</>
                ) : (
                  'Test saved authentication'
                )}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={mode.onReplace}>
                Replace cookies
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={mode.onUseWithoutLogin}>
                Use without login
              </Button>
            </div>
          </div>
        ) : (
          <>
            <CookieRowsInput idPrefix={mode.idPrefix} value={mode.rows} onChange={mode.onChange} />
            {mode.savedCredentials?.hasPrefilledNames && (
              <p className="text-xs text-gray-600">
                Cookie names are prefilled from saved authentication. Paste fresh values only.
              </p>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={isPending || !baseUrl}
              onClick={() => onProbe({ cookies: buildCrawlerCookies(mode.rows, baseUrl) })}
            >
              {isPending ? (
                <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />Testing...</>
              ) : (
                'Test authentication'
              )}
            </Button>
          </>
        )}
      </div>

      {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}
      {result && <AuthProbeFeedback result={result} />}

      <div className="flex gap-2 pt-1">
        <Button
          type="button"
          size="sm"
          disabled={result?.classification !== 'auth_ok'}
          onClick={() => onNext(result?.auth_guard ?? null)}
        >
          {m.admin_connectors_webcrawler_next()}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onBack}>
          {m.admin_connectors_webcrawler_back()}
        </Button>
      </div>
    </div>
  )
}
