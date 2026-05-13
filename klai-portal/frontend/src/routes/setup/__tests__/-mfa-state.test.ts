import { describe, expect, it } from 'vitest'
import {
  canResendEmailOtp,
  createInitialEmailOtpState,
  emailOtpReducer,
  initialMfaPageState,
  initialPasskeyState,
  initialTotpState,
  mfaPageReducer,
  passkeyReducer,
  totpReducer,
} from '../_components/-mfa-state'

describe('MFA setup state machine', () => {
  it('keeps the setup flow on method pick until a method is selected', () => {
    expect(mfaPageReducer(initialMfaPageState, { type: 'continue' })).toEqual(initialMfaPageState)
  })

  it('moves from method pick to setup and back to a clean picker', () => {
    const selected = mfaPageReducer(initialMfaPageState, { type: 'selectMethod', method: 'totp' })
    const setup = mfaPageReducer(selected, { type: 'continue' })

    expect(setup).toEqual({ step: 'setup', selectedMethod: 'totp' })
    expect(mfaPageReducer(setup, { type: 'back' })).toEqual(initialMfaPageState)
  })

  it('preserves the completed method when moving to done', () => {
    const selected = mfaPageReducer(initialMfaPageState, { type: 'selectMethod', method: 'passkey' })
    const setup = mfaPageReducer(selected, { type: 'continue' })

    expect(mfaPageReducer(setup, { type: 'complete' })).toEqual({
      step: 'done',
      selectedMethod: 'passkey',
    })
  })
})

describe('passkey setup reducer', () => {
  it('clears prior errors when starting browser registration', () => {
    const failed = passkeyReducer(initialPasskeyState, { type: 'fail', error: 'failed' })

    expect(passkeyReducer(failed, { type: 'start' })).toEqual({
      loading: true,
      error: null,
    })
  })

  it('treats user cancellation as a non-error terminal state', () => {
    const loading = passkeyReducer(initialPasskeyState, { type: 'start' })

    expect(passkeyReducer(loading, { type: 'cancel' })).toEqual(initialPasskeyState)
  })
})

describe('email OTP reducer', () => {
  it('moves to verification after sending the first code and starts resend cooldown', () => {
    const sending = emailOtpReducer(createInitialEmailOtpState(1_000), { type: 'sendStart' })
    const verifying = emailOtpReducer(sending, { type: 'sendSuccess', resendAt: 31_000 })

    expect(verifying).toMatchObject({
      phase: 'verify',
      sending: false,
      error: null,
      resendAt: 31_000,
    })
    expect(canResendEmailOtp(verifying)).toBe(false)
    expect(canResendEmailOtp(emailOtpReducer(verifying, { type: 'tick', now: 31_000 }))).toBe(true)
  })

  it('keeps verification codes numeric and six digits long', () => {
    const state = emailOtpReducer(createInitialEmailOtpState(), { type: 'setCode', code: '12a34-567' })

    expect(state.code).toBe('123456')
  })
})

describe('TOTP reducer', () => {
  it('loads a QR payload into the ready state', () => {
    expect(totpReducer(initialTotpState, { type: 'loadSuccess', uri: 'otpauth://x', secret: 'ABC' })).toMatchObject({
      status: 'ready',
      uri: 'otpauth://x',
      secret: 'ABC',
      loadError: null,
    })
  })

  it('clears stale QR payloads after a retry', () => {
    const ready = totpReducer(initialTotpState, { type: 'loadSuccess', uri: 'otpauth://x', secret: 'ABC' })

    expect(totpReducer(ready, { type: 'retry' })).toEqual({
      ...initialTotpState,
      retryCount: 1,
    })
  })

  it('keeps confirmation codes numeric and six digits long', () => {
    const state = totpReducer(initialTotpState, { type: 'setCode', code: '9a876543' })

    expect(state.code).toBe('987654')
  })
})
