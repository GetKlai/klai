/**
 * `docs/ui-standards.md` closes with a Rules Ledger: every normative rule in
 * the document, with a stable ID, an RFC 2119 level, and a declared
 * verification mode.
 *
 * The ledger exists because the document could not previously answer its own
 * most useful question — how much of this is actually checked? "Three parts
 * are not prose" was true when it was written and would have stayed on the
 * page unchanged as checks were added or removed. A coverage claim that
 * nothing verifies is the same failure mode as a stale token value: it reads
 * as fact, it loads into every agent session, and it is wrong silently.
 *
 * So the claim has to be load-bearing. This test makes it so, in both
 * directions:
 *
 *   - A row marked `automated` must name a check that EXISTS and, for lint
 *     rules, is WIRED into eslint.config.js at a level other than 'off'. Test
 *     paths named by automated or assisted rows must also be collected by
 *     Vitest. A rule cannot advertise enforcement it does not have. This is
 *     the "a test that has never failed has not been tested" guard, applied
 *     to the ledger itself.
 *   - A design check that exists must appear in the ledger. Adding
 *     tests/design/foo.test.ts without ledgering it fails here, so coverage
 *     cannot grow silently either.
 *   - The summary counts near the top of the document must match the table at
 *     the bottom, so the headline number cannot drift from the rows.
 *
 * `none` is deliberately part of the vocabulary. It means we decided not to
 * check a rule, and the reason is written down beside it. An empty or
 * placeholder reason is a failure: the point of the mode is the reason.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'

const FRONTEND_ROOT = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
)
const STANDARDS = path.join(FRONTEND_ROOT, 'docs', 'ui-standards.md')
const ESLINT_CONFIG = path.join(FRONTEND_ROOT, 'eslint.config.js')
const VITEST_CONFIG = path.join(FRONTEND_ROOT, 'vitest.config.ts')
const ESLINT_RULES_DIR = path.join(FRONTEND_ROOT, 'eslint-rules')
const DESIGN_TESTS_DIR = path.join(FRONTEND_ROOT, 'tests', 'design')

/** This file. It guards the ledger; it is not itself a ledgered design rule. */
const SELF = 'rules-ledger.test.ts'

/**
 * ESLint rules in the `klai/` plugin that enforce architecture rather than the
 * UI contract. They are real rules and stay wired; they just are not part of
 * this document, so the reverse-coverage check must not demand a ledger row.
 */
const NON_DESIGN_ESLINT_RULES = ['no-cross-route-import', 'no-direct-kb-querykey']

const LEVELS = ['must', 'must-not', 'should', 'should-not']
const MODES = ['automated', 'assisted', 'manual', 'none']

type LedgerRow = {
  id: string
  rule: string
  level: string
  verification: string
  check: string
}

/**
 * Every `klai/` rule switched on in eslint.config.js, mapped to its severity.
 *
 * ESLint accepts two equivalent forms and this file already uses both:
 * `'klai/foo': 'error'` and `'no-console': ['error', { allow: [...] }]`. A
 * regex that only understands the string form silently fails both ways — it
 * would call an array-configured rule "not configured" when the ledger names
 * it, and would miss it entirely when the ledger does not. Parse both.
 */
function wiredKlaiRules(config: string): Map<string, string> {
  const wired = new Map<string, string>()

  for (const m of config.matchAll(
    /['"]klai\/([a-z-]+)['"]\s*:\s*(?:['"]([a-z]+)['"]|\[\s*['"]([a-z]+)['"])/g,
  )) {
    wired.set(m[1], m[2] ?? m[3])
  }

  return wired
}

const doc = fs.readFileSync(STANDARDS, 'utf8')

function ledgerRows(): LedgerRow[] {
  const start = doc.indexOf('## Rules Ledger')
  if (start < 0) throw new Error('ui-standards.md lost its Rules Ledger heading')

  return doc
    .slice(start)
    .split('\n')
    .filter((line) => line.startsWith('| KLAI-UI-'))
    .map((line) => {
      const cells = line.split('|').slice(1, -1).map((c) => c.trim())
      const [id, rule, level, verification, check] = cells
      return { id, rule, level, verification, check }
    })
}

function retiredIds(): string[] {
  const ledgerStart = doc.indexOf('## Rules Ledger')
  const heading = '### Retired IDs'
  const start = doc.indexOf(heading, ledgerStart)
  if (start < 0) throw new Error('ui-standards.md lost its Retired IDs heading')

  const sectionStart = start + heading.length
  const nextHeading = doc.indexOf('\n### ', sectionStart)
  const section = doc.slice(sectionStart, nextHeading < 0 ? undefined : nextHeading)

  return section
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('- '))
    .map((line) => {
      const id = line.slice(2).trim()
      if (!/^KLAI-UI-\d{3}$/.test(id)) {
        throw new Error(`invalid retired ID entry: ${line}`)
      }
      return id
    })
}

/** Backticked tokens in a cell — how the ledger names a check. */
function codeSpans(cell: string): string[] {
  return [...cell.matchAll(/`([^`]+)`/g)].map((m) => m[1])
}

function missingCheckFiles(
  checkRows: LedgerRow[],
  pathsOnly = false,
): string[] {
  const missing: string[] = []

  for (const row of checkRows) {
    for (const ref of codeSpans(row.check)) {
      const isPath = ref.startsWith('src/') || ref.startsWith('tests/')
      if (pathsOnly && !isPath) continue

      if (ref.startsWith('klai/')) {
        const file = path.join(
          ESLINT_RULES_DIR,
          `${ref.slice('klai/'.length)}.js`,
        )
        if (!fs.existsSync(file)) {
          missing.push(
            `${row.id} -> ${ref} (no ${path.relative(FRONTEND_ROOT, file)})`,
          )
        }
      } else if (isPath) {
        if (!fs.existsSync(path.join(FRONTEND_ROOT, ref))) {
          missing.push(`${row.id} -> ${ref}`)
        }
      } else {
        missing.push(`${row.id} -> ${ref} (unrecognized check reference)`)
      }
    }
  }

  return missing
}

function vitestExcludePatterns(config: string): string[] {
  const exclude = /exclude\s*:\s*\[([\s\S]*?)\]/.exec(config)
  if (!exclude) throw new Error('vitest.config.ts lost its test.exclude array')

  return [...exclude[1].matchAll(/['"]([^'"]+)['"]/g)].map((m) => m[1])
}

/** Minimal matching for the single-star and recursive-star globs Vitest uses. */
function matchesGlob(file: string, glob: string): boolean {
  let source = ''
  const regexSpecials = new Set([
    '\\', '^', '$', '.', '+', '?', '(', ')', '[', ']', '{', '}', '|',
  ])

  for (let i = 0; i < glob.length; i += 1) {
    if (glob.startsWith('**/', i)) {
      source += '(?:.*/)?'
      i += 2
    } else if (glob.startsWith('**', i)) {
      source += '.*'
      i += 1
    } else if (glob[i] === '*') {
      source += '[^/]*'
    } else {
      source += regexSpecials.has(glob[i]) ? `\\${glob[i]}` : glob[i]
    }
  }

  return new RegExp(`^${source}$`).test(file.replaceAll(path.sep, '/'))
}

const rows = ledgerRows()
const retired = retiredIds()

describe('ui-standards.md Rules Ledger', () => {
  it('has rows', () => {
    expect(rows.length).toBeGreaterThan(0)
  })

  it('uses well-formed, unique, ascending IDs', () => {
    const ids = rows.map((r) => r.id)

    expect(ids.filter((id) => !/^KLAI-UI-\d{3}$/.test(id))).toEqual([])
    expect(
      ids.filter((id, i) => ids.indexOf(id) !== i),
      'a ledger ID is never reused — retire the number instead',
    ).toEqual([])
    expect([...ids].sort(), 'keep the ledger in ID order').toEqual(ids)
  })

  it('never reuses a retired ID', () => {
    const active = new Set(rows.map((r) => r.id))

    expect(
      retired.filter((id, i) => retired.indexOf(id) !== i),
      'list each retired ID once',
    ).toEqual([])
    expect(
      retired.filter((id) => active.has(id)),
      'a retired ledger ID must never return to the active table',
    ).toEqual([])
  })

  it('declares a known level and verification mode on every row', () => {
    const badLevel = rows.filter((r) => !LEVELS.includes(r.level))
    const badMode = rows.filter((r) => !MODES.includes(r.verification))

    expect(badLevel.map((r) => `${r.id}: ${r.level}`)).toEqual([])
    expect(badMode.map((r) => `${r.id}: ${r.verification}`)).toEqual([])
  })

  it('gives every unchecked rule a written reason', () => {
    // The reason is the entire value of the `none` mode. Without it the row
    // says "we do not check this" and stops exactly where it gets useful.
    const unreasoned = rows
      .filter((r) => r.verification === 'none')
      .filter((r) => r.check.length < 30 || /^(todo|tbd|n\/a)/i.test(r.check))

    expect(
      unreasoned.map((r) => r.id),
      'a `none` row must say why we decided not to check it',
    ).toEqual([])
  })
})

describe('ledger rows marked `automated`', () => {
  const automated = rows.filter((r) => r.verification === 'automated')

  it('name at least one check each', () => {
    const nameless = automated.filter((r) => codeSpans(r.check).length === 0)

    expect(
      nameless.map((r) => r.id),
      'an `automated` row must name its check in backticks',
    ).toEqual([])
  })

  it('name checks that exist', () => {
    expect(missingCheckFiles(automated)).toEqual([])
  })

  it('name lint rules that are actually switched on', () => {
    // A rule file that exists but is not in eslint.config.js — or is set to
    // 'off' — enforces nothing. That is the exact gap the ledger must not be
    // able to hide.
    const wired = wiredKlaiRules(fs.readFileSync(ESLINT_CONFIG, 'utf8'))
    const unwired: string[] = []

    for (const row of automated) {
      for (const ref of codeSpans(row.check).filter((r) => r.startsWith('klai/'))) {
        const severity = wired.get(ref.slice('klai/'.length))
        if (!severity) unwired.push(`${row.id} -> ${ref} is not configured in eslint.config.js`)
        else if (severity === 'off') unwired.push(`${row.id} -> ${ref} is configured 'off'`)
      }
    }

    expect(unwired).toEqual([])
  })
})

describe('ledger rows marked `assisted`', () => {
  const assisted = rows.filter((r) => r.verification === 'assisted')

  it('name referenced src/ and tests/ files that exist', () => {
    expect(missingCheckFiles(assisted, true)).toEqual([])
  })
})

describe('ledgered test checks', () => {
  const testRefs = rows
    .filter((r) => r.verification === 'automated' || r.verification === 'assisted')
    .flatMap((r) => codeSpans(r.check).map((ref) => ({ id: r.id, ref })))
    .filter(({ ref }) => /\.(?:test|spec)\.[jt]sx?$/.test(ref))

  it('are collected by Vitest', () => {
    const patterns = vitestExcludePatterns(fs.readFileSync(VITEST_CONFIG, 'utf8'))
    const excluded = testRefs.flatMap(({ id, ref }) =>
      patterns
        .filter((pattern) => matchesGlob(ref, pattern))
        .map((pattern) => `${id} -> ${ref} (excluded by ${pattern})`),
    )

    expect(excluded).toEqual([])
  })
})

describe('every design check is ledgered', () => {
  const automatedRefs = new Set(
    rows.filter((r) => r.verification === 'automated').flatMap((r) => codeSpans(r.check)),
  )

  it('covers every test in tests/design/', () => {
    const unledgered = fs
      .readdirSync(DESIGN_TESTS_DIR)
      .filter((f) => /\.(?:test|spec)\.tsx?$/.test(f) && f !== SELF)
      .filter((f) => !automatedRefs.has(`tests/design/${f}`))

    expect(
      unledgered,
      'add a Rules Ledger row naming this check, or the document under-reports its own coverage',
    ).toEqual([])
  })

  it('covers every wired klai/ design lint rule', () => {
    const wired = wiredKlaiRules(fs.readFileSync(ESLINT_CONFIG, 'utf8'))

    const unledgered = [...wired.entries()]
      .filter(([, severity]) => severity !== 'off')
      .map(([name]) => name)
      .filter((name) => !NON_DESIGN_ESLINT_RULES.includes(name))
      .filter((name) => !automatedRefs.has(`klai/${name}`))

    expect(unledgered).toEqual([])
  })
})

describe('the enforcement summary matches the ledger', () => {
  it('reports the same counts', () => {
    // The count line wraps in the source, so match on collapsed whitespace.
    const summary =
      /\*\*(\d+) rules — (\d+) automated, (\d+) assisted, (\d+) manual, (\d+) deliberately unchecked\.\*\*/.exec(
        doc.replace(/\s+/g, ' '),
      )

    expect(summary, 'the What Is Enforced section lost its count line').not.toBeNull()

    const [, total, automated, assisted, manual, none] = summary!.map(Number)
    const count = (mode: string) => rows.filter((r) => r.verification === mode).length

    expect({ total, automated, assisted, manual, none }).toEqual({
      total: rows.length,
      automated: count('automated'),
      assisted: count('assisted'),
      manual: count('manual'),
      none: count('none'),
    })
  })

  it('lists exactly the automated checks in its table', () => {
    // The summary table and the ledger are two renderings of one fact. They
    // drift the moment a check is added to one and not the other.
    const start = doc.indexOf('## What Is Enforced (and what is not)')
    const end = doc.indexOf('## Component Library Reference')
    const section = doc.slice(start, end)

    const inTable = new Set(
      [
        ...section.matchAll(
          /`(klai\/[a-z-]+|tests\/design\/[a-z-]+\.(?:test|spec)\.tsx?)`/g,
        ),
      ].map((m) => m[1]),
    )
    const inLedger = new Set(
      rows.filter((r) => r.verification === 'automated').flatMap((r) => codeSpans(r.check)),
    )

    expect([...inTable].sort()).toEqual([...inLedger].sort())
  })
})
