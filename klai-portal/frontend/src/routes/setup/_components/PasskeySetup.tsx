import { useState } from 'react'
import { Fingerprint } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import { authLogger } from '@/lib/logger'

// ── WebAuthn helpers ────────────────────────────────────────────────────────

function base64urlToBuffer(base64url: string): ArrayBuffer {
  const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(base64)
  const buffer = new ArrayBuffer(binary.length)
  const view = new Uint8Array(buffer)
  for (let i = 0; i < binary.length; i++) view[i] = binary.charCodeAt(i)
  return buffer
}

function bufferToBase64url(buffer: ArrayBuffer): string {
  const view = new Uint8Array(buffer)
  let binary = ''
  for (const byte of view) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
}

function encodeCredential(credential: PublicKeyCredential): object {
  const resp = credential.response as AuthenticatorAttestationResponse
  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(resp.clientDataJSON),
      attestationObject: bufferToBase64url(resp.attestationObject),
    },
  }
}

// ── Component ───────────────────────────────────────────────────────────────

interface PasskeySetupProps {
  token: string
  onSuccess: () => void
  onBack: () => void
}

export function PasskeySetup({ token, onSuccess, onBack }: PasskeySetupProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const supportsPasskeys = typeof window !== 'undefined' && !!window.PublicKeyCredential

  async function handleSetup() {
    setError(null)
    setLoading(true)
    try {
      // 1. Get registration options from backend
      const { passkey_id, options } = await apiFetch<{ passkey_id: string; options: { publicKey: PublicKeyCredentialCreationOptions & { challenge: string; user: { id: string } & PublicKeyCredentialUserEntity; excludeCredentials?: { id: string; type: string }[] } } }>(`/api/auth/passkey/setup`, token, {
        method: 'POST',
      })
      // Zitadel wraps WebAuthn options under publicKeyCredentialCreationOptions.publicKey
      const pk = options.publicKey

      // 2. Decode binary fields from base64url (Zitadel encodes them as base64url strings)
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

      // 3. Trigger browser native dialog
      const credential = await navigator.credentials.create({ publicKey }) as PublicKeyCredential | null
      if (!credential) throw new Error('cancelled')

      // 4. Send credential to backend for verification
      await apiFetch(`/api/auth/passkey/confirm`, token, {
        method: 'POST',
        body: JSON.stringify({
          passkey_id,
          public_key_credential: encodeCredential(credential),
        }),
      })

      onSuccess()
    } catch (err) {
      // NotAllowedError = user dismissed the browser dialog — not a real error
      if (err instanceof DOMException && err.name === 'NotAllowedError') {
        setError(null)
      } else {
        authLogger.error('Passkey setup failed', err)
        setError(m.setup_mfa_passkey_error_failed())
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-2xl font-bold text-[var(--color-foreground)]">
          {m.setup_mfa_passkey_heading()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.setup_mfa_passkey_body()}
        </p>
      </div>

      {!supportsPasskeys ? (
        <p className="rounded-lg bg-[var(--color-warning-bg)] px-3 py-2 text-sm text-[var(--color-warning-text)]">
          {m.setup_mfa_passkey_error_unsupported()}
        </p>
      ) : (
        <>
          {error && (
            <p className="rounded-lg bg-[var(--color-destructive-bg)] px-3 py-2 text-sm text-[var(--color-destructive-text)]">{error}</p>
          )}
          <Button
            size="lg"
            className="w-full gap-3"
            onClick={handleSetup}
            disabled={loading}
          >
            <Fingerprint size={16} />
            {loading ? m.setup_mfa_passkey_loading() : m.setup_mfa_passkey_button()}
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
