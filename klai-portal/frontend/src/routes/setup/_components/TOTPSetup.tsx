import { useEffect, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import QRCode from 'react-qr-code'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import { authLogger } from '@/lib/logger'

interface TOTPSetupProps {
  token: string
  onSuccess: () => void
  onBack: () => void
}

export function TOTPSetup({ token, onSuccess, onBack }: TOTPSetupProps) {
  const [uri, setUri] = useState<string | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    setUri(null)
    setSecret(null)
    setLoadError(null)
    apiFetch<{ uri: string; secret: string }>(`/api/auth/totp/setup`, token, { method: 'POST' })
      .then((data) => {
        setUri(data.uri)
        setSecret(data.secret)
      })
      .catch((err) => { authLogger.warn('TOTP setup QR fetch failed', err); setLoadError(m.error_connection()) })
  }, [token, retryCount])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await apiFetch(`/api/auth/totp/confirm`, token, {
        method: 'POST',
        body: JSON.stringify({ code }),
      })
      onSuccess()
    } catch {
      setError(m.error_connection())
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.setup_2fa_heading()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.setup_2fa_subheading()}
        </p>
      </div>

      {loadError ? (
        <div className="space-y-3 text-center">
          <p className="text-sm text-[var(--color-destructive-text)]">{loadError}</p>
          <button
            onClick={() => setRetryCount((c) => c + 1)}
            className="text-xs text-[var(--color-purple-muted)] hover:underline"
          >
            {m.setup_2fa_retry()}
          </button>
        </div>
      ) : (
        <>
          <div className="flex flex-col items-center gap-4">
            {uri ? (
              <div className="rounded-xl border border-[var(--color-border)] bg-white p-4">
                <QRCode value={uri} size={180} />
              </div>
            ) : (
              <div className="flex h-[212px] w-[212px] items-center justify-center rounded-xl border border-[var(--color-border)] bg-white">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-purple-accent)] border-t-transparent" />
              </div>
            )}
            {secret && (
              <details className="w-full text-center">
                <summary className="cursor-pointer select-none text-xs text-[var(--color-purple-muted)] hover:underline">
                  {m.setup_2fa_manual_label()}
                </summary>
                <p className="mt-2 break-all rounded-lg bg-[var(--color-border)] px-3 py-2 font-mono text-xs tracking-widest">
                  {secret}
                </p>
              </details>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label htmlFor="totp-code" className="block text-sm font-medium text-[var(--color-foreground)]">
                {m.setup_2fa_field_code()}
              </label>
              <input
                id="totp-code"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                required
                autoComplete="one-time-code"
                autoFocus
                className="w-full rounded-lg border border-[var(--color-border)] bg-white px-3 py-2 text-center font-mono text-lg tracking-widest outline-none transition focus:border-[var(--color-purple-accent)] focus:ring-2 focus:ring-[var(--color-purple-accent)]/20"
              />
            </div>
            {error && (
              <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
            )}
            <Button
              type="submit"
              size="lg"
              className="w-full gap-3"
              disabled={loading || code.length !== 6 || !uri}
            >
              {loading ? m.setup_2fa_submit_loading() : m.setup_2fa_submit()}
              {!loading && <ArrowRight size={16} />}
            </Button>
          </form>
        </>
      )}

      <button
        type="button"
        onClick={onBack}
        className="block text-xs text-[var(--color-purple-muted)] hover:underline"
      >
        {m.setup_mfa_back()}
      </button>
    </div>
  )
}
