import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
)
const UI_DIR = path.join(FRONTEND_ROOT, 'src', 'components', 'ui')
const STANDARDS = path.join(FRONTEND_ROOT, 'docs', 'ui-standards.md')
const START_MARKER = '<!-- generated:component-reference -->'
const END_MARKER = '<!-- /generated:component-reference -->'
const GUIDELINES_START_MARKER = '<!-- generated:component-guidelines -->'
const GUIDELINES_END_MARKER = '<!-- /generated:component-guidelines -->'
const GUIDELINE_IDS_PREFIX = '<!-- generated:component-guideline-ids '
const VALID_CANONICAL = new Set(['yes', 'restricted', 'feature'])
const VALID_GUIDELINE_LEVELS = new Set([
  'must',
  'must-not',
  'should',
  'should-not',
])

const args = process.argv.slice(2)
if (args.some((arg) => arg !== '--check') || args.length > 1) {
  console.error('Usage: node scripts/generate-component-reference.mjs [--check]')
  process.exit(2)
}

function commentTags(comment) {
  const lines = comment
    .split('\n')
    .map((line) => line.replace(/^\s*\* ?/, '').trim())
  const tags = []
  let current = null

  for (const line of lines) {
    const match = /^@(\S+)(?:\s+(.*))?$/.exec(line)
    if (match) {
      current = { tag: match[1], value: match[2] ?? '' }
      tags.push(current)
    } else if (current && line) {
      current.value += ` ${line}`
    } else {
      current = null
    }
  }

  return tags.map(({ tag, value }) => ({
    tag,
    value: value.replace(/\s+/g, ' ').trim(),
  }))
}

function tagValue(comment, tag) {
  return commentTags(comment).find((entry) => entry.tag === tag)?.value ?? null
}

function guidelineMetadata(comment, file) {
  const guidelines = []
  let current = null

  for (const entry of commentTags(comment)) {
    if (entry.tag === 'guideline') {
      const match = /^(KLAI-UI-\d{3})\s+(\S+)\s+(.+)$/.exec(entry.value)
      if (!match) {
        throw new Error(
          `${file} has invalid @guideline ${JSON.stringify(entry.value)}; ` +
            'expected <KLAI-UI-NNN> <level> <rule>',
        )
      }

      const [, id, level, rule] = match
      if (!VALID_GUIDELINE_LEVELS.has(level)) {
        throw new Error(
          `${file} has invalid guideline level ${JSON.stringify(level)}; ` +
            'expected must, must-not, should, or should-not',
        )
      }

      current = { id, level, rule, rationale: null }
      guidelines.push(current)
    } else if (entry.tag === 'rationale') {
      if (!current) {
        throw new Error(`${file} has @rationale without a preceding @guideline`)
      }
      if (current.rationale) {
        throw new Error(`${file} has more than one @rationale for ${current.id}`)
      }
      if (!entry.value) {
        throw new Error(`${file} has an empty @rationale for ${current.id}`)
      }
      current.rationale = entry.value
    }
  }

  return guidelines
}

function componentMetadata(file) {
  const source = fs.readFileSync(path.join(UI_DIR, file), 'utf8')
  const header = /^\/\*\*([\s\S]*?)\*\//.exec(source)
  if (!header) throw new Error(`${file} is missing a header doc comment`)

  const purpose = tagValue(header[1], 'purpose')
  if (!purpose) throw new Error(`${file} is missing @purpose in its header doc comment`)

  const canonical = tagValue(header[1], 'canonical') ?? 'yes'
  if (!VALID_CANONICAL.has(canonical)) {
    throw new Error(
      `${file} has invalid @canonical ${JSON.stringify(canonical)}; ` +
        'expected yes, restricted, or feature',
    )
  }

  return {
    name: file.replace(/\.tsx?$/, ''),
    purpose,
    canonical,
    guidelines: guidelineMetadata(header[1], file),
    source,
  }
}

function findMatching(source, start, open, close) {
  let depth = 0
  let quote = null
  let lineComment = false
  let blockComment = false

  for (let index = start; index < source.length; index += 1) {
    const char = source[index]
    const next = source[index + 1]

    if (lineComment) {
      if (char === '\n') lineComment = false
      continue
    }
    if (blockComment) {
      if (char === '*' && next === '/') {
        blockComment = false
        index += 1
      }
      continue
    }
    if (quote) {
      if (char === '\\') {
        index += 1
      } else if (char === quote) {
        quote = null
      }
      continue
    }
    if (char === '/' && next === '/') {
      lineComment = true
      index += 1
      continue
    }
    if (char === '/' && next === '*') {
      blockComment = true
      index += 1
      continue
    }
    if (char === "'" || char === '"' || char === '`') {
      quote = char
      continue
    }
    if (char === open) depth += 1
    if (char === close) {
      depth -= 1
      if (depth === 0) return index
    }
  }

  throw new Error(`Unclosed ${open} starting at offset ${start}`)
}

function objectEntries(source, openBrace) {
  const end = findMatching(source, openBrace, '{', '}')
  const entries = []
  let depth = 1
  let quote = null

  for (let index = openBrace + 1; index < end; index += 1) {
    const char = source[index]

    if (quote) {
      if (char === '\\') index += 1
      else if (char === quote) quote = null
      continue
    }
    if (char === "'" || char === '"' || char === '`') {
      quote = char
      continue
    }
    if (char === '{' || char === '[' || char === '(') {
      depth += 1
      continue
    }
    if (char === '}' || char === ']' || char === ')') {
      depth -= 1
      continue
    }
    if (depth !== 1) continue

    const match = /^(?:([A-Za-z_$][\w$]*)|['"]([^'"]+)['"])\s*:/.exec(
      source.slice(index),
    )
    if (!match) continue

    let valueStart = index + match[0].length
    while (/\s/.test(source[valueStart])) valueStart += 1
    entries.push({ key: match[1] ?? match[2], valueStart })
    index += match[0].length - 1
  }

  return entries
}

function cvaAxes(source) {
  const axes = new Map()

  for (const call of source.matchAll(/\bcva\s*\(/g)) {
    const openParen = call.index + call[0].lastIndexOf('(')
    const closeParen = findMatching(source, openParen, '(', ')')
    const body = source.slice(openParen + 1, closeParen)
    const variants = /\bvariants\s*:\s*\{/.exec(body)
    if (!variants) continue

    const variantsOpen = openParen + 1 + variants.index + variants[0].lastIndexOf('{')
    for (const axis of objectEntries(source, variantsOpen)) {
      if (source[axis.valueStart] !== '{') continue
      const values = objectEntries(source, axis.valueStart).map((entry) => entry.key)
      const known = axes.get(axis.key) ?? []
      axes.set(axis.key, [...new Set([...known, ...values])])
    }
  }

  return axes
}

function axisLabel(axis) {
  if (axis === 'variant') return 'variants'
  if (axis === 'size') return 'sizes'
  return axis.endsWith('s') ? axis : `${axis}s`
}

function escapeCell(value) {
  return value.replaceAll('|', '\\|')
}

function componentFiles() {
  return fs
    .readdirSync(UI_DIR, { withFileTypes: true })
    .filter(
      (entry) =>
        entry.isFile() &&
        (entry.name.endsWith('.tsx') || entry.name === 'use-list-controls.ts'),
    )
    .map((entry) => entry.name)
    .sort((left, right) => left.localeCompare(right, 'en'))
}

function generatedTable(components) {
  const rows = components.map((component) => {
    const axes = cvaAxes(component.source)
    const variants = [...axes]
      .map(([axis, values]) => `${axisLabel(axis)}: ${values.join('/')}`)
      .join('; ')
    const purpose = variants
      ? `${component.purpose} (${variants})`
      : component.purpose

    return `| \`${component.name}\` | ${escapeCell(purpose)} | ${
      component.canonical[0].toUpperCase() + component.canonical.slice(1)
    } |`
  })

  return [
    '| Component | Purpose | Canonical? |',
    '|---|---|---|',
    ...rows,
  ].join('\n')
}

function markedRange(doc, startMarker, endMarker, label) {
  const start = doc.indexOf(startMarker)
  const end = doc.indexOf(endMarker)
  if (start < 0 || end < 0 || end < start) {
    throw new Error(`docs/ui-standards.md is missing ${label} markers`)
  }

  return {
    start,
    end: end + endMarker.length,
    tableStart: start + startMarker.length + 1,
    tableEnd: end - 1,
  }
}

function parseLedgerRows(doc) {
  const ledgerStart = doc.indexOf('## Rules Ledger')
  const retiredStart = doc.indexOf('### Retired IDs', ledgerStart)
  if (ledgerStart < 0 || retiredStart < 0) {
    throw new Error('docs/ui-standards.md is missing its Rules Ledger')
  }

  return doc
    .slice(ledgerStart, retiredStart)
    .split('\n')
    .filter((line) => line.startsWith('| KLAI-UI-'))
    .map((line) => {
      const [id, rule, level, verification, check] = line
        .split('|')
        .slice(1, -1)
        .map((cell) => cell.trim())
      return { id, rule, level, verification, check }
    })
}

function previousGuidelineIds(doc) {
  const start = doc.indexOf(GUIDELINES_START_MARKER)
  if (start < 0) return new Set()

  const lineStart = doc.indexOf(GUIDELINE_IDS_PREFIX, start)
  const lineEnd = doc.indexOf(' -->', lineStart)
  if (lineStart < 0 || lineEnd < 0) {
    throw new Error('component-guidelines block is missing its generated ID list')
  }

  return new Set(
    doc
      .slice(lineStart + GUIDELINE_IDS_PREFIX.length, lineEnd)
      .split(' ')
      .filter(Boolean),
  )
}

function componentGuidelines(components) {
  const byId = new Map()

  for (const component of components) {
    for (const guideline of component.guidelines) {
      const existing = byId.get(guideline.id)
      if (existing) {
        const fields = ['level', 'rule', 'rationale']
        const conflict = fields.find((field) => existing[field] !== guideline[field])
        if (conflict) {
          throw new Error(
            `${guideline.id} differs between ${existing.file} and ` +
              `${component.name} at ${conflict}`,
          )
        }
        existing.files.push(component.name)
      } else {
        byId.set(guideline.id, {
          ...guideline,
          file: component.name,
          files: [component.name],
        })
      }
    }
  }

  return byId
}

function ledgerTable(doc, components) {
  const currentRows = parseLedgerRows(doc)
  const currentById = new Map(currentRows.map((row) => [row.id, row]))
  const guidelines = componentGuidelines(components)
  const generatedIds = new Set([
    ...previousGuidelineIds(doc),
    ...guidelines.keys(),
  ])
  const rows = currentRows.filter((row) => !generatedIds.has(row.id))

  for (const guideline of guidelines.values()) {
    const current = currentById.get(guideline.id)
    if (!current) {
      throw new Error(
        `${guideline.id} has no ledger row supplying its verification mode`,
      )
    }
    rows.push({
      id: guideline.id,
      rule: guideline.rule,
      level: guideline.level,
      verification: current.verification,
      check: current.check,
    })
  }

  rows.sort((left, right) => left.id.localeCompare(right.id, 'en'))
  const ids = [...guidelines.keys()].sort((left, right) => left.localeCompare(right, 'en'))
  const renderedRows = rows.map(
    (row) =>
      `| ${row.id} | ${escapeCell(row.rule)} | ${row.level} | ` +
      `${row.verification} | ${escapeCell(row.check)} |`,
  )

  return [
    `${GUIDELINE_IDS_PREFIX}${ids.join(' ')} -->`,
    '| ID | Rule | Level | Verification | Check / reason |',
    '|---|---|---|---|---|',
    ...renderedRows,
  ].join('\n')
}

function guidelineRange(doc) {
  if (doc.includes(GUIDELINES_START_MARKER)) {
    return markedRange(
      doc,
      GUIDELINES_START_MARKER,
      GUIDELINES_END_MARKER,
      'component-guidelines',
    )
  }

  const ledgerStart = doc.indexOf('## Rules Ledger')
  const tableStart = doc.indexOf('| ID | Rule | Level | Verification | Check / reason |', ledgerStart)
  const retiredStart = doc.indexOf('### Retired IDs', tableStart)
  if (ledgerStart < 0 || tableStart < 0 || retiredStart < 0) {
    throw new Error('docs/ui-standards.md is missing its Rules Ledger table')
  }

  let tableEnd = retiredStart
  while (doc[tableEnd - 1] === '\n') tableEnd -= 1
  return { start: tableStart, end: tableEnd }
}

function updateEnforcementCounts(doc) {
  const rows = parseLedgerRows(doc)
  const count = (mode) => rows.filter((row) => row.verification === mode).length
  const replacement =
    `The count today: **${rows.length} rules — ${count('automated')} automated, ` +
    `${count('assisted')} assisted,\n${count('manual')} manual, ` +
    `${count('none')} deliberately unchecked.**`
  const pattern =
    /The count today: \*\*\d+ rules — \d+ automated, \d+ assisted,\s*\d+ manual, \d+ deliberately unchecked\.\*\*/
  if (!pattern.test(doc)) {
    throw new Error('docs/ui-standards.md is missing its enforcement count line')
  }
  return doc.replace(pattern, replacement)
}

function diffSummary(actual, expected) {
  const left = actual.split('\n')
  const right = expected.split('\n')
  const lengths = Array.from({ length: left.length + 1 }, () =>
    Array(right.length + 1).fill(0),
  )

  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      lengths[i][j] = left[i] === right[j]
        ? lengths[i + 1][j + 1] + 1
        : Math.max(lengths[i + 1][j], lengths[i][j + 1])
    }
  }

  const changes = []
  let i = 0
  let j = 0
  while (i < left.length || j < right.length) {
    if (left[i] === right[j]) {
      i += 1
      j += 1
    } else if (j < right.length && (i === left.length || lengths[i][j + 1] >= lengths[i + 1][j])) {
      changes.push(`+ ${right[j]}`)
      j += 1
    } else {
      changes.push(`- ${left[i]}`)
      i += 1
    }
  }
  return changes.join('\n')
}

const doc = fs.readFileSync(STANDARDS, 'utf8')
const components = componentFiles().map((file) => componentMetadata(file))
const componentRange = markedRange(
  doc,
  START_MARKER,
  END_MARKER,
  'component-reference',
)
const componentTable = generatedTable(components)
let expectedDoc =
  `${doc.slice(0, componentRange.tableStart)}${componentTable}` +
  `${doc.slice(componentRange.tableEnd)}`
const rulesRange = guidelineRange(expectedDoc)
const rulesTable = ledgerTable(expectedDoc, components)
const rulesBlock =
  `${GUIDELINES_START_MARKER}\n${rulesTable}\n${GUIDELINES_END_MARKER}`
expectedDoc =
  `${expectedDoc.slice(0, rulesRange.start)}${rulesBlock}` +
  `${expectedDoc.slice(rulesRange.end)}`
expectedDoc = updateEnforcementCounts(expectedDoc)

if (args[0] === '--check') {
  if (doc !== expectedDoc) {
    console.error('Generated component documentation is stale:')
    console.error(diffSummary(doc, expectedDoc))
    console.error('\nRun `npm run docs:components` to regenerate it.')
    process.exit(1)
  }
  console.log('Component reference and guidelines are up to date.')
} else {
  fs.writeFileSync(STANDARDS, expectedDoc)
  console.log(
    `Generated ${components.length} component rows and ` +
      `${componentGuidelines(components).size} guideline rows.`,
  )
}
