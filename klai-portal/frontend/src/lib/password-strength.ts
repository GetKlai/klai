import type { SignupPasswordPolicy } from '@/lib/password-policy'

export type SignupPasswordIssue =
  | 'too_short'
  | 'too_predictable'

export interface SignupPasswordStrength {
  score: number
  issues: SignupPasswordIssue[]
  isAcceptable: boolean
  estimated: boolean
}

type ZxcvbnCheck = (password: string, userInputs?: string[]) => { score: number }

let zxcvbnLoader: Promise<ZxcvbnCheck> | null = null

async function loadZxcvbn() {
  zxcvbnLoader ??= Promise.all([
    import('@zxcvbn-ts/core'),
    import('@zxcvbn-ts/language-common'),
    import('@zxcvbn-ts/language-en'),
  ]).then(([core, commonPackage, enPackage]) => {
    const zxcvbn = new core.ZxcvbnFactory({
      translations: enPackage.translations,
      graphs: commonPackage.adjacencyGraphs,
      dictionary: {
        ...commonPackage.dictionary,
        ...enPackage.dictionary,
      },
    })
    return (password, userInputs) => zxcvbn.check(password, userInputs)
  })
  return zxcvbnLoader
}

export function basicSignupPasswordIssues(password: string, policy: SignupPasswordPolicy) {
  const issues: SignupPasswordIssue[] = []
  if (Array.from(password).length < policy.min_length) {
    issues.push('too_short')
  }
  return issues
}

export function estimateSignupPasswordStrength(
  password: string,
  policy: SignupPasswordPolicy | null,
): SignupPasswordStrength {
  if (!policy) {
    return {
      score: 0,
      issues: [],
      isAcceptable: false,
      estimated: true,
    }
  }
  const issues = basicSignupPasswordIssues(password, policy)
  const score = issues.length === 0 ? Math.max(0, Math.min(2, policy.min_score - 1)) : 0
  return {
    score,
    issues,
    isAcceptable: false,
    estimated: true,
  }
}

export async function evaluateSignupPassword(
  password: string,
  userInputs: Array<string | null | undefined>,
  policy: SignupPasswordPolicy,
): Promise<SignupPasswordStrength> {
  const context = userInputs.map((value) => value?.trim()).filter((value): value is string => Boolean(value))
  const issues = basicSignupPasswordIssues(password, policy)
  const check = await loadZxcvbn()
  const result = check(password, context)
  const rawScore = Number(result.score)
  if (password.length > 0 && rawScore < policy.min_score) {
    issues.push('too_predictable')
  }
  return {
    score: rawScore,
    issues,
    isAcceptable: issues.length === 0,
    estimated: false,
  }
}
