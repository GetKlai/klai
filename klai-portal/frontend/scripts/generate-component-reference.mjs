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
const VALID_CANONICAL = new Set(['yes', 'restricted', 'feature'])

const args = process.argv.slice(2)
if (args.some((arg) => arg !== '--check') || args.length > 1) {
  console.error('Usage: node scripts/generate-component-reference.mjs [--check]')
  process.exit(2)
}

function tagValue(comment, tag) {
  const lines = comment
    .split('\n')
    .map((line) => line.replace(/^\s*\* ?/, '').trim())
  const start = lines.findIndex((line) => line.startsWith(`@${tag} `))
  if (start < 0) return null

  const value = [lines[start].slice(tag.length + 2)]
  for (let index = start + 1; index < lines.length; index += 1) {
    if (!lines[index] || lines[index].startsWith('@')) break
    value.push(lines[index])
  }
  return value.join(' ').replace(/\s+/g, ' ').trim()
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

function generatedTable() {
  const files = fs
    .readdirSync(UI_DIR, { withFileTypes: true })
    .filter(
      (entry) =>
        entry.isFile() &&
        (entry.name.endsWith('.tsx') || entry.name === 'use-list-controls.ts'),
    )
    .map((entry) => entry.name)
    .sort((left, right) => left.localeCompare(right, 'en'))

  const rows = files.map((file) => {
    const component = componentMetadata(file)
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

function generatedRange(doc) {
  const start = doc.indexOf(START_MARKER)
  const end = doc.indexOf(END_MARKER)
  if (start < 0 || end < 0 || end < start) {
    throw new Error('docs/ui-standards.md is missing component-reference markers')
  }

  return {
    start,
    end: end + END_MARKER.length,
    tableStart: start + START_MARKER.length + 1,
    tableEnd: end - 1,
  }
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
const range = generatedRange(doc)
const expected = generatedTable()
const actual = doc.slice(range.tableStart, range.tableEnd)

if (args[0] === '--check') {
  if (actual !== expected) {
    console.error('Component Library Reference is stale:')
    console.error(diffSummary(actual, expected))
    console.error('\nRun `npm run docs:components` to regenerate it.')
    process.exit(1)
  }
  console.log('Component Library Reference is up to date.')
} else {
  const block = `${START_MARKER}\n${expected}\n${END_MARKER}`
  fs.writeFileSync(
    STANDARDS,
    `${doc.slice(0, range.start)}${block}${doc.slice(range.end)}`,
  )
  console.log(`Generated ${expected.split('\n').length - 2} component rows.`)
}
