import { createLazyFileRoute } from '@tanstack/react-router'
import { useReducer } from 'react'
import { ArrowRight, Fingerprint, Mail, Shield, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'
import { useProtectedRoute } from '@/hooks/useProtectedRoute'
import { useAuth } from '@/lib/auth'
import { useLocale } from '@/lib/locale'
import * as m from '@/paraglide/messages'
import { EmailOTPSetup } from './_components/-EmailOTPSetup'
import { MfaMethodCard } from './_components/-MfaMethodCard'
import { PasskeySetup } from './_components/-PasskeySetup'
import { TOTPSetup } from './_components/-TOTPSetup'
import { initialMfaPageState, mfaPageReducer } from './_components/-mfa-state'

export const Route = createLazyFileRoute('/setup/mfa')({
  component: SetupMFAPage,
})

function SetupMFAPage() {
  useLocale()
  const auth = useAuth()
  const { user: currentUser, canRender } = useProtectedRoute()
  const [state, dispatch] = useReducer(mfaPageReducer, initialMfaPageState)

  const mfaPolicy = currentUser?.mfa_policy ?? 'optional'
  const isRequired = mfaPolicy === 'required'
  const email = auth.user?.profile?.email as string ?? ''

  function redirectAfterSetup() {
    window.location.replace(currentUser?.isAdmin ? '/admin' : '/app')
  }

  function handleSuccess() {
    dispatch({ type: 'complete' })
    setTimeout(redirectAfterSetup, 1500)
  }

  if (!canRender) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  const leftContent = (
    <>
      <h1 className="text-2xl font-semibold leading-tight">
        {m.setup_mfa_hero_heading()}
        <br />
        <span className="text-[var(--color-rl-accent)]">{m.setup_mfa_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-rl-cream)]">
        {m.setup_mfa_hero_body()}
      </p>
      <div className="flex items-center gap-3 text-sm text-[var(--color-rl-cream)]">
        <Shield size={16} className="shrink-0 text-[var(--color-rl-accent)]" />
        {m.setup_mfa_hero_methods()}
      </div>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent}>
      {state.step === 'done' && (
        <div className="space-y-4 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-foreground)]">
            <ShieldCheck size={22} className="text-[var(--color-rl-cream)]" />
          </div>
          <p className="text-xl font-semibold text-gray-900">
            {m.setup_mfa_done_heading()}
          </p>
          <p className="text-sm text-gray-400">
            {m.setup_mfa_done_body()}
          </p>
        </div>
      )}

      {state.step === 'pick' && (
        <div className="space-y-5">
          <div className="space-y-1">
            <h2 className="text-xl font-semibold text-gray-900">
              {m.setup_mfa_heading()}
            </h2>
            <p className="text-sm text-gray-400">
              {m.setup_mfa_subheading()}
            </p>
          </div>

          <div className="space-y-3">
            <MfaMethodCard
              icon={<Fingerprint size={20} />}
              title={m.setup_mfa_passkey_title()}
              description={m.setup_mfa_passkey_description()}
              recommended
              selected={state.selectedMethod === 'passkey'}
              onClick={() => dispatch({ type: 'selectMethod', method: 'passkey' })}
            />
            <MfaMethodCard
              icon={<Mail size={20} />}
              title={m.setup_mfa_email_title()}
              description={m.setup_mfa_email_description()}
              selected={state.selectedMethod === 'email'}
              onClick={() => dispatch({ type: 'selectMethod', method: 'email' })}
            />
            <MfaMethodCard
              icon={<Shield size={20} />}
              title={m.setup_mfa_totp_title()}
              description={m.setup_mfa_totp_description()}
              selected={state.selectedMethod === 'totp'}
              onClick={() => dispatch({ type: 'selectMethod', method: 'totp' })}
            />
          </div>

          <Button
            size="lg"
            className="w-full gap-3"
            disabled={!state.selectedMethod}
            onClick={() => dispatch({ type: 'continue' })}
          >
            {m.setup_mfa_continue()}
            <ArrowRight size={16} />
          </Button>

          {!isRequired && (
            <div className="text-center">
              <button
                type="button"
                onClick={redirectAfterSetup}
                className="text-xs text-gray-400 hover:text-[var(--color-rl-accent-dark)] hover:underline"
              >
                {m.setup_mfa_skip()}
              </button>
            </div>
          )}
        </div>
      )}

      {state.step === 'setup' && state.selectedMethod === 'passkey' && (
        <PasskeySetup onSuccess={handleSuccess} onBack={() => dispatch({ type: 'back' })} />
      )}
      {state.step === 'setup' && state.selectedMethod === 'email' && (
        <EmailOTPSetup email={email} onSuccess={handleSuccess} onBack={() => dispatch({ type: 'back' })} />
      )}
      {state.step === 'setup' && state.selectedMethod === 'totp' && (
        <TOTPSetup onSuccess={handleSuccess} onBack={() => dispatch({ type: 'back' })} />
      )}
    </AuthPageLayout>
  )
}
