import { useEffect, useReducer } from 'react'
import { ArrowRight } from 'lucide-react'
import QRCode from 'react-qr-code'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/apiFetch'
import { authLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { initialTotpState, totpReducer } from './-mfa-state'

export function TOTPSetup({
  onSuccess,
  onBack,
}: {
  onSuccess: () => void
  onBack: () => void
}) {
  const [state, dispatch] = useReducer(totpReducer, initialTotpState)

  useEffect(() => {
    let cancelled = false
    dispatch({ type: 'loadStart' })
    apiFetch<{ uri: string; secret: string }>(`/api/auth/totp/setup`, { method: 'POST' })
      .then((data) => {
        if (!cancelled) dispatch({ type: 'loadSuccess', uri: data.uri, secret: data.secret })
      })
      .catch((err) => {
        authLogger.warn('TOTP setup QR fetch failed', err)
        if (!cancelled) dispatch({ type: 'loadFail', error: m.error_connection() })
      })
    return () => {
      cancelled = true
    }
  }, [state.retryCount])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    dispatch({ type: 'confirmStart' })
    try {
      await apiFetch(`/api/auth/totp/confirm`, {
        method: 'POST',
        body: JSON.stringify({ code: state.code }),
      })
      dispatch({ type: 'confirmFinish' })
      onSuccess()
    } catch {
      dispatch({ type: 'confirmFail', error: m.error_connection() })
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-gray-900">
          {m.setup_2fa_heading()}
        </h2>
        <p className="text-sm text-gray-400">
          {m.setup_2fa_subheading()}
        </p>
      </div>

      {state.status === 'error' ? (
        <div className="space-y-3 text-center">
          <p className="text-sm text-[var(--color-destructive-text)]">{state.loadError}</p>
          <button
            onClick={() => dispatch({ type: 'retry' })}
            className="text-xs text-[var(--color-rl-accent-dark)] hover:underline"
          >
            {m.setup_2fa_retry()}
          </button>
        </div>
      ) : (
        <>
          <div className="flex flex-col items-center gap-4">
            {state.uri ? (
              <div className="rounded-xl border border-gray-200 bg-[var(--color-background)] p-4">
                <QRCode value={state.uri} size={180} />
              </div>
            ) : (
              <div className="flex h-[212px] w-[212px] items-center justify-center rounded-xl border border-gray-200 bg-[var(--color-background)]">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
              </div>
            )}
            {state.secret && (
              <details className="w-full text-center">
                <summary className="cursor-pointer select-none text-xs text-[var(--color-rl-accent-dark)] hover:underline">
                  {m.setup_2fa_manual_label()}
                </summary>
                <p className="mt-2 break-all rounded-lg bg-gray-200 px-3 py-2 font-mono text-xs tracking-widest">
                  {state.secret}
                </p>
              </details>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="totp-code" className="block text-sm font-medium text-gray-900">
                {m.setup_2fa_field_code()}
              </label>
              <input
                id="totp-code"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={state.code}
                onChange={(e) => dispatch({ type: 'setCode', code: e.target.value })}
                required
                autoComplete="one-time-code"
                autoFocus
                className="w-full rounded-lg border border-gray-200 bg-[var(--color-background)] px-3 py-2 text-center font-mono text-base tracking-widest outline-none transition focus:ring-2 focus:ring-[var(--color-ring)]"
              />
            </div>
            {state.submitError && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{state.submitError}</p>
            )}
            <Button
              type="submit"
              size="lg"
              className="w-full gap-3"
              disabled={state.confirming || state.code.length !== 6 || !state.uri}
            >
              {state.confirming ? m.setup_2fa_submit_loading() : m.setup_2fa_submit()}
              {!state.confirming && <ArrowRight size={16} />}
            </Button>
          </form>
        </>
      )}

      <button
        type="button"
        onClick={onBack}
        className="block text-xs text-[var(--color-rl-accent-dark)] hover:underline"
      >
        {m.setup_mfa_back()}
      </button>
    </div>
  )
}
