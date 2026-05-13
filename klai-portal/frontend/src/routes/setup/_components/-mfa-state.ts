export type MfaMethod = 'passkey' | 'email' | 'totp'

export type MfaPageStep = 'pick' | 'setup' | 'done'

export type MfaPageState =
  | { step: 'pick'; selectedMethod: MfaMethod | null }
  | { step: 'setup'; selectedMethod: MfaMethod }
  | { step: 'done'; selectedMethod: MfaMethod | null }

export type MfaPageAction =
  | { type: 'selectMethod'; method: MfaMethod }
  | { type: 'continue' }
  | { type: 'back' }
  | { type: 'complete' }

export const initialMfaPageState: MfaPageState = {
  step: 'pick',
  selectedMethod: null,
}

export function mfaPageReducer(state: MfaPageState, action: MfaPageAction): MfaPageState {
  switch (action.type) {
    case 'selectMethod':
      return { step: 'pick', selectedMethod: action.method }
    case 'continue':
      return state.step === 'pick' && state.selectedMethod
        ? { step: 'setup', selectedMethod: state.selectedMethod }
        : state
    case 'back':
      return initialMfaPageState
    case 'complete':
      return { step: 'done', selectedMethod: state.selectedMethod }
    default:
      return state
  }
}

export type PasskeyState = {
  loading: boolean
  error: string | null
}

export type PasskeyAction =
  | { type: 'start' }
  | { type: 'cancel' }
  | { type: 'fail'; error: string }
  | { type: 'finish' }

export const initialPasskeyState: PasskeyState = {
  loading: false,
  error: null,
}

export function passkeyReducer(state: PasskeyState, action: PasskeyAction): PasskeyState {
  switch (action.type) {
    case 'start':
      return { loading: true, error: null }
    case 'cancel':
      return { loading: false, error: null }
    case 'fail':
      return { loading: false, error: action.error }
    case 'finish':
      return { ...state, loading: false }
    default:
      return state
  }
}

export type EmailOtpState = {
  phase: 'send' | 'verify'
  sending: boolean
  code: string
  verifying: boolean
  error: string | null
  resendAt: number | null
  now: number
}

export type EmailOtpAction =
  | { type: 'sendStart' }
  | { type: 'sendSuccess'; resendAt: number }
  | { type: 'sendFail'; error: string }
  | { type: 'resendStart' }
  | { type: 'resendSuccess'; resendAt: number }
  | { type: 'resendFail'; error: string }
  | { type: 'setCode'; code: string }
  | { type: 'verifyStart' }
  | { type: 'verifyFail'; error: string }
  | { type: 'verifyFinish' }
  | { type: 'tick'; now: number }

export function createInitialEmailOtpState(now = Date.now()): EmailOtpState {
  return {
    phase: 'send',
    sending: false,
    code: '',
    verifying: false,
    error: null,
    resendAt: null,
    now,
  }
}

export function emailOtpReducer(state: EmailOtpState, action: EmailOtpAction): EmailOtpState {
  switch (action.type) {
    case 'sendStart':
      return { ...state, sending: true, error: null }
    case 'sendSuccess':
      return { ...state, phase: 'verify', sending: false, error: null, resendAt: action.resendAt }
    case 'sendFail':
      return { ...state, sending: false, error: action.error }
    case 'resendStart':
      return { ...state, sending: true, error: null }
    case 'resendSuccess':
      return { ...state, sending: false, error: null, resendAt: action.resendAt }
    case 'resendFail':
      return { ...state, sending: false, error: action.error }
    case 'setCode':
      return { ...state, code: action.code.replace(/\D/g, '').slice(0, 6) }
    case 'verifyStart':
      return { ...state, verifying: true, error: null }
    case 'verifyFail':
      return { ...state, verifying: false, error: action.error }
    case 'verifyFinish':
      return { ...state, verifying: false }
    case 'tick':
      return { ...state, now: action.now }
    default:
      return state
  }
}

export function canResendEmailOtp(state: EmailOtpState): boolean {
  return state.resendAt === null || state.now >= state.resendAt
}

export type TotpState = {
  status: 'loading' | 'ready' | 'error'
  uri: string | null
  secret: string | null
  loadError: string | null
  code: string
  submitError: string | null
  confirming: boolean
  retryCount: number
}

export type TotpAction =
  | { type: 'loadStart' }
  | { type: 'loadSuccess'; uri: string; secret: string }
  | { type: 'loadFail'; error: string }
  | { type: 'retry' }
  | { type: 'setCode'; code: string }
  | { type: 'confirmStart' }
  | { type: 'confirmFail'; error: string }
  | { type: 'confirmFinish' }

export const initialTotpState: TotpState = {
  status: 'loading',
  uri: null,
  secret: null,
  loadError: null,
  code: '',
  submitError: null,
  confirming: false,
  retryCount: 0,
}

export function totpReducer(state: TotpState, action: TotpAction): TotpState {
  switch (action.type) {
    case 'loadStart':
      return { ...state, status: 'loading', uri: null, secret: null, loadError: null }
    case 'loadSuccess':
      return {
        ...state,
        status: 'ready',
        uri: action.uri,
        secret: action.secret,
        loadError: null,
      }
    case 'loadFail':
      return { ...state, status: 'error', loadError: action.error, uri: null, secret: null }
    case 'retry':
      return { ...initialTotpState, retryCount: state.retryCount + 1 }
    case 'setCode':
      return { ...state, code: action.code.replace(/\D/g, '').slice(0, 6) }
    case 'confirmStart':
      return { ...state, confirming: true, submitError: null }
    case 'confirmFail':
      return { ...state, confirming: false, submitError: action.error }
    case 'confirmFinish':
      return { ...state, confirming: false }
    default:
      return state
  }
}
