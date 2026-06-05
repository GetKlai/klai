import { API_BASE } from '@/lib/api'

export interface SignupPasswordPolicy {
  min_length: number
  min_score: number
  require_uppercase: boolean
  require_lowercase: boolean
  require_number: boolean
  require_symbol: boolean
}

let passwordPolicyPromise: Promise<SignupPasswordPolicy> | null = null
let passwordPolicyFetchedAt = 0

const PASSWORD_POLICY_CACHE_TTL_MS = 5 * 60 * 1000

export function loadSignupPasswordPolicy(options: { force?: boolean } = {}) {
  const now = Date.now()
  if (options.force || (passwordPolicyPromise && now - passwordPolicyFetchedAt > PASSWORD_POLICY_CACHE_TTL_MS)) {
    passwordPolicyPromise = null
  }

  passwordPolicyPromise ??= fetch(`${API_BASE}/api/auth/password-policy`, {
    headers: { Accept: 'application/json' },
  })
    .then(async (response) => {
      if (!response.ok) throw new Error(`Password policy request failed: ${response.status}`)
      const policy = (await response.json()) as Partial<SignupPasswordPolicy>
      if (
        typeof policy.min_length !== 'number' ||
        typeof policy.min_score !== 'number' ||
        typeof policy.require_uppercase !== 'boolean' ||
        typeof policy.require_lowercase !== 'boolean' ||
        typeof policy.require_number !== 'boolean' ||
        typeof policy.require_symbol !== 'boolean'
      ) {
        throw new Error('Password policy response is invalid')
      }
      return policy as SignupPasswordPolicy
    })
    .then((policy) => {
      passwordPolicyFetchedAt = Date.now()
      return policy
    })
    .catch((error) => {
      passwordPolicyPromise = null
      passwordPolicyFetchedAt = 0
      throw error
    })
  return passwordPolicyPromise
}

export function resetSignupPasswordPolicyCacheForTests() {
  passwordPolicyPromise = null
  passwordPolicyFetchedAt = 0
}
