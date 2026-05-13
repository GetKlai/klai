import { useReducer } from 'react'
import { Fingerprint } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/apiFetch'
import { authLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { encodeCredential, base64urlToBuffer } from './-mfa-webauthn'
import { initialPasskeyState, passkeyReducer } from './-mfa-state'

export function PasskeySetup({
  onSuccess,
  onBack,
}: {
  onSuccess: () => void
  onBack: () => void
}) {
  const [state, dispatch] = useReducer(passkeyReducer, initialPasskeyState)

  const supportsPasskeys = typeof window !== 'undefined' && !!window.PublicKeyCredential

  async function handleSetup() {
    dispatch({ type: 'start' })
    try {
      const { passkey_id, options } = await apiFetch<{ passkey_id: string; options: { publicKey: PublicKeyCredentialCreationOptions & { challenge: string; user: { id: string } & PublicKeyCredentialUserEntity; excludeCredentials?: { id: string; type: string }[] } } }>(`/api/auth/passkey/setup`, {
        method: 'POST',
      })
      const pk = options.publicKey

      const publicKey: PublicKeyCredentialCreationOptions = {
        ...pk,
        challenge: base64urlToBuffer(pk.challenge),
        user: {
          ...pk.user,
          id: base64urlToBuffer(pk.user.id),
        },
        excludeCredentials: pk.excludeCredentials?.map((c: { id: string; type: string }) => ({
          ...c,
          id: base64urlToBuffer(c.id),
          type: c.type as 'public-key',
        })) ?? [],
      }

      const credential = await navigator.credentials.create({ publicKey }) as PublicKeyCredential | null
      if (!credential) throw new Error('cancelled')

      await apiFetch(`/api/auth/passkey/confirm`, {
        method: 'POST',
        body: JSON.stringify({
          passkey_id,
          public_key_credential: encodeCredential(credential),
        }),
      })

      dispatch({ type: 'finish' })
      onSuccess()
    } catch (err) {
      if (err instanceof DOMException && err.name === 'NotAllowedError') {
        dispatch({ type: 'cancel' })
      } else {
        authLogger.error('Passkey setup failed', err)
        dispatch({ type: 'fail', error: m.setup_mfa_passkey_error_failed() })
      }
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-gray-900">
          {m.setup_mfa_passkey_heading()}
        </h2>
        <p className="text-sm text-gray-400">
          {m.setup_mfa_passkey_body()}
        </p>
      </div>

      {!supportsPasskeys ? (
        <p className="rounded-lg bg-[var(--color-warning-bg)] px-3 py-2 text-sm text-[var(--color-warning-text)]">
          {m.setup_mfa_passkey_error_unsupported()}
        </p>
      ) : (
        <>
          {state.error && (
            <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{state.error}</p>
          )}
          <Button
            size="lg"
            className="w-full gap-3"
            onClick={handleSetup}
            disabled={state.loading}
          >
            <Fingerprint size={16} />
            {state.loading ? m.setup_mfa_passkey_loading() : m.setup_mfa_passkey_button()}
          </Button>
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
