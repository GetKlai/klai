import { KeyRound, Loader2 } from 'lucide-react'
import { CookieRowsInput } from '@/components/knowledge/CookieRowsInput'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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

// The test URL must stay on the same origin as the site being crawled -
// it's the destination cookies (including decrypted saved ones) get sent
// to. A cross-origin value would let anyone who can edit the connector
// exfiltrate its stored session cookies to an arbitrary domain.
function isSameOrigin(a: string, b: string): boolean {
  try {
    return new URL(a).origin === new URL(b).origin
  } catch {
    return false
  }
}

type CrawlerAuthSetupStepProps = {
  baseUrl: string
  testUrl: string
  onTestUrlChange: (url: string) => void
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
  testUrl,
  onTestUrlChange,
  mode,
  isPending,
  error,
  result,
  onProbe,
  onNext,
  onBack,
}: CrawlerAuthSetupStepProps) {
  const hasSavedCredentials = mode.kind === 'saved' || mode.savedCredentials !== undefined
  const effectiveTestUrl = testUrl || baseUrl
  const testUrlCrossOrigin = Boolean(effectiveTestUrl) && Boolean(baseUrl) && !isSameOrigin(effectiveTestUrl, baseUrl)

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 p-4 space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="wc-auth-test-url">{m.admin_connectors_webcrawler_test_url()}</Label>
          <Input
            id="wc-auth-test-url"
            type="url"
            placeholder={baseUrl}
            value={testUrl}
            onChange={(e) => onTestUrlChange(e.target.value)}
          />
          <p className="text-xs text-gray-600">{m.admin_connectors_webcrawler_test_url_hint()}</p>
          {testUrlCrossOrigin && (
            <p className="text-xs text-[var(--color-destructive)]">
              {m.admin_connectors_webcrawler_test_url_cross_origin()}
            </p>
          )}
        </div>

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
                disabled={isPending || !baseUrl || testUrlCrossOrigin}
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
              disabled={isPending || !baseUrl || testUrlCrossOrigin}
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
