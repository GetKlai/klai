export const SIGNUP_PASSWORD_MIN_LENGTH = 12
export const SIGNUP_PASSWORD_MIN_SCORE = 3

export type SignupPasswordIssue =
  | 'too_short'
  | 'missing_uppercase'
  | 'missing_lowercase'
  | 'missing_number'
  | 'missing_symbol'
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
    core.zxcvbnOptions.setOptions({
      translations: enPackage.translations,
      graphs: commonPackage.adjacencyGraphs,
      dictionary: {
        ...commonPackage.dictionary,
        ...enPackage.dictionary,
      },
    })
    return core.zxcvbn
  })
  return zxcvbnLoader
}

export function hasSignupPasswordSymbol(password: string) {
  return Array.from(password).some((char) => /[^\p{L}\p{N}\s]/u.test(char))
}

export function hasSignupPasswordUppercase(password: string) {
  return Array.from(password).some((char) => /\p{Lu}/u.test(char))
}

export function hasSignupPasswordLowercase(password: string) {
  return Array.from(password).some((char) => /\p{Ll}/u.test(char))
}

export function hasSignupPasswordNumber(password: string) {
  return Array.from(password).some((char) => /\p{N}/u.test(char))
}

export function basicSignupPasswordIssues(password: string) {
  const issues: SignupPasswordIssue[] = []
  if (password.length < SIGNUP_PASSWORD_MIN_LENGTH) {
    issues.push('too_short')
  }
  if (!hasSignupPasswordUppercase(password)) {
    issues.push('missing_uppercase')
  }
  if (!hasSignupPasswordLowercase(password)) {
    issues.push('missing_lowercase')
  }
  if (!hasSignupPasswordNumber(password)) {
    issues.push('missing_number')
  }
  if (!hasSignupPasswordSymbol(password)) {
    issues.push('missing_symbol')
  }
  return issues
}

export function estimateSignupPasswordStrength(password: string): SignupPasswordStrength {
  const issues = basicSignupPasswordIssues(password)
  const score = issues.length === 0 ? 2 : 0
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
): Promise<SignupPasswordStrength> {
  const context = userInputs.map((value) => value?.trim()).filter((value): value is string => Boolean(value))
  const issues = basicSignupPasswordIssues(password)
  const check = await loadZxcvbn()
  const result = check(password, context)
  const rawScore = Number(result.score)
  if (password.length > 0 && rawScore < SIGNUP_PASSWORD_MIN_SCORE) {
    issues.push('too_predictable')
  }
  return {
    score: rawScore,
    issues,
    isAcceptable: issues.length === 0,
    estimated: false,
  }
}
