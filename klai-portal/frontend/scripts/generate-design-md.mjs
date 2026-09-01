import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
)
const UI_DIR = path.join(FRONTEND_ROOT, 'src', 'components', 'ui')
const INDEX_CSS = path.join(FRONTEND_ROOT, 'src', 'index.css')
const STANDARDS = path.join(FRONTEND_ROOT, 'docs', 'ui-standards.md')
const OUTPUT = path.join(FRONTEND_ROOT, 'DESIGN.md')
const TAILWIND_THEME = fileURLToPath(import.meta.resolve('tailwindcss/theme.css'))
const VALID_CANONICAL = new Set(['yes', 'restricted', 'feature'])
const VALID_GUIDELINE_LEVELS = new Set([
  'must',
  'must-not',
  'should',
  'should-not',
])

const args = process.argv.slice(2)
if (args.some((arg) => arg !== '--check') || args.length > 1) {
  console.error('Usage: node scripts/generate-design-md.mjs [--check]')
  process.exit(2)
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
      if (char === '\\') index += 1
      else if (char === quote) quote = null
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

function themeDeclarations(source) {
  const theme = /@theme(?:\s+inline)?\s*\{/.exec(source)
  if (!theme) throw new Error('src/index.css is missing its @theme block')

  const open = theme.index + theme[0].lastIndexOf('{')
  const close = findMatching(source, open, '{', '}')
  return [...source.slice(open + 1, close).matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)]
    .map((match) => ({ name: match[1], value: match[2].trim() }))
}

function cssDeclarations(source) {
  return [...source.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)]
    .map((match) => ({ name: match[1], value: match[2].trim() }))
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

function tagValue(tags, tag) {
  return tags.find((entry) => entry.tag === tag)?.value ?? null
}

function guidelineMetadata(tags, file) {
  const guidelines = []
  let current = null

  for (const entry of tags) {
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

/**
 * Every source file's text, concatenated. Used to decide which type-scale steps
 * the portal actually uses, so the emitted scale reflects our decisions rather
 * than Tailwind's defaults.
 */
function readSourceFiles() {
  const root = path.join(FRONTEND_ROOT, 'src')
  const chunks = []
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (entry.name === 'paraglide') continue
        walk(full)
      } else if (/\.tsx?$/.test(entry.name)) {
        chunks.push(fs.readFileSync(full, 'utf8'))
      }
    }
  }
  walk(root)
  return chunks.join('\n')
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

function componentMetadata(file) {
  const source = fs.readFileSync(path.join(UI_DIR, file), 'utf8')
  const header = /^\/\*\*([\s\S]*?)\*\//.exec(source)
  if (!header) throw new Error(`${file} is missing a header doc comment`)

  const tags = commentTags(header[1])
  const purpose = tagValue(tags, 'purpose')
  if (!purpose) throw new Error(`${file} is missing @purpose in its header doc comment`)

  const canonical = tagValue(tags, 'canonical') ?? 'yes'
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
    guidelines: guidelineMetadata(tags, file),
    avoids: tags.filter((entry) => entry.tag === 'avoid').map((entry) => entry.value),
    axes: cvaAxes(source),
  }
}

function markdownSection(doc, heading) {
  const marker = `## ${heading}`
  const start = doc.indexOf(marker)
  if (start < 0) throw new Error(`docs/ui-standards.md is missing its ${heading} section`)

  const bodyStart = doc.indexOf('\n', start) + 1
  const nextHeading = doc.indexOf('\n## ', bodyStart)
  return doc.slice(bodyStart, nextHeading < 0 ? undefined : nextHeading).trim()
}

function opening(doc) {
  const firstLineEnd = doc.indexOf('\n')
  const nextHeading = doc.indexOf('\n## ', firstLineEnd)
  if (firstLineEnd < 0 || nextHeading < 0) {
    throw new Error('docs/ui-standards.md is missing its opening')
  }
  return doc.slice(firstLineEnd + 1, nextHeading).trim()
}

function yamlScalar(value) {
  return JSON.stringify(value)
}

function colorValue(value) {
  return value.replace(/^var\(--color-([\w-]+)\)$/, '{colors.$1}')
}

function yamlFrontMatter(theme, tailwind, components) {
  const colors = theme.filter(({ name }) => name.startsWith('color-'))
  const fonts = theme.filter(({ name }) => name.startsWith('font-'))
  const radii = theme.filter(({ name }) => name.startsWith('radius-'))
  const spacing = theme.filter(({ name }) => name.startsWith('spacing-'))
  // Only the steps this portal actually uses. Emitting Tailwind's full default
  // scale would claim a 9xl display size we have never rendered, and this file
  // is read by agents that cannot tell a real decision from an inherited default.
  const usedTextSizes = new Set(
    [...readSourceFiles().matchAll(/\btext-(xs|sm|base|lg|xl|[0-9]xl)\b/g)].map((m) => `text-${m[1]}`),
  )
  const textSizes = tailwind
    .filter(({ name }) => /^text-[\w]+$/.test(name))
    .filter(({ name }) => usedTextSizes.has(name))
    .map((token) => ({
      ...token,
      lineHeight: tailwind.find(({ name }) => name === `${token.name}--line-height`)?.value,
    }))
  const lines = [
    '---',
    'version: alpha',
    `name: ${yamlScalar('Klai Portal')}`,
    `description: ${yamlScalar('Generated design contract for the Klai portal frontend.')}`,
    'colors:',
    ...colors.map(
      ({ name, value }) => `  ${name.slice('color-'.length)}: ${yamlScalar(colorValue(value))}`,
    ),
    'typography:',
    ...fonts.map(
      ({ name, value }) =>
        `  ${name.slice('font-'.length)}: { fontFamily: ${yamlScalar(value)} }`,
    ),
    ...textSizes.map(
      ({ name, value, lineHeight }) =>
        `  ${name}: { fontSize: ${yamlScalar(value)}, lineHeight: ${yamlScalar(lineHeight)} }`,
    ),
    'rounded:',
    ...radii.map(
      ({ name, value }) => `  ${name.slice('radius-'.length)}: ${yamlScalar(value)}`,
    ),
    spacing.length > 0 ? 'spacing:' : 'spacing: {}',
    ...spacing.map(
      ({ name, value }) => `  ${name.slice('spacing-'.length)}: ${yamlScalar(value)}`,
    ),
    'components:',
    ...components.map((component) => {
      const axes = [...component.axes]
      if (axes.length === 0) return `  ${component.name}: {}`
      const values = axes
        .map(([axis, options]) => `${axis}: ${yamlScalar(options.join(' | '))}`)
        .join(', ')
      return `  ${component.name}: { ${values} }`
    }),
    '---',
  ]

  return lines.join('\n')
}

function typographySection(theme, tailwind, css) {
  const fonts = theme.filter(({ name }) => name.startsWith('font-'))
  const textSizes = tailwind.filter(({ name }) => /^text-[\w]+$/.test(name))
  const rootSize = /html\s*\{[\s\S]*?font-size:\s*([^;]+);/.exec(css)?.[1].trim()
  if (!rootSize) throw new Error('src/index.css is missing its root font size')

  const fontRows = fonts.map(
    ({ name, value }) => `| \`${name}\` | \`${value}\` |`,
  )
  const scaleRows = textSizes.map(({ name, value }) => {
    const lineHeight = tailwind.find(
      (token) => token.name === `${name}--line-height`,
    )?.value
    return `| \`${name}\` | \`${value}\` | \`${lineHeight}\` |`
  })

  return [
    `The portal root is **${rootSize}** of the browser default. The rem-based type scale therefore grows together with spacing, controls, radii, and widths.`,
    '',
    '| Font token | Family |',
    '|---|---|',
    ...fontRows,
    '',
    '| Type token | Font size | Line height |',
    '|---|---|---|',
    ...scaleRows,
  ].join('\n')
}

function elevationSection(theme) {
  const shadows = theme.filter(({ name }) => name.startsWith('shadow-'))
  if (shadows.length === 0) {
    return 'The portal declares no shared elevation or shadow token scale; elevation remains component-owned and must not be invented as a system.'
  }

  return [
    'The theme declares these shared elevation tokens:',
    '',
    ...shadows.map(({ name, value }) => `- \`${name}\`: \`${value}\`.`),
  ].join('\n')
}

function shapesSection(theme, standards) {
  const radii = theme.filter(({ name }) => name.startsWith('radius-'))
  return [
    '| Radius token | Value |',
    '|---|---|',
    ...radii.map(({ name, value }) => `| \`${name}\` | \`${value}\` |`),
    '',
    markdownSection(standards, 'Cards'),
  ].join('\n')
}

function componentsSection(components) {
  return components.map((component) => {
    const lines = [`### \`${component.name}\``, '', component.purpose]
    const axes = [...component.axes]
    if (axes.length > 0) {
      lines.push(
        '',
        ...axes.map(
          ([axis, values]) => `- **${axis}:** ${values.map((value) => `\`${value}\``).join(', ')}`,
        ),
      )
    }
    for (const guideline of component.guidelines) {
      lines.push('', `- **${guideline.level} (${guideline.id}):** ${guideline.rule}`)
      if (guideline.rationale) lines.push(`  Rationale: ${guideline.rationale}`)
    }
    for (const avoid of component.avoids) lines.push('', `- **Avoid:** ${avoid}`)
    return lines.join('\n')
  }).join('\n\n')
}

function uniqueGuidelines(components) {
  const byId = new Map()
  for (const component of components) {
    for (const guideline of component.guidelines) {
      const existing = byId.get(guideline.id)
      if (existing && (existing.level !== guideline.level || existing.rule !== guideline.rule)) {
        throw new Error(`${guideline.id} has conflicting component guidelines`)
      }
      if (!existing) byId.set(guideline.id, guideline)
    }
  }
  return [...byId.values()].sort((left, right) => left.id.localeCompare(right.id, 'en'))
}

function dosAndDontsSection(standards, components) {
  const guidelines = uniqueGuidelines(components)
  const must = guidelines.filter(({ level }) => level === 'must')
  const mustNot = guidelines.filter(({ level }) => level === 'must-not')

  return [
    "### Do's",
    '',
    ...must.map(({ id, rule }) => `- **${id}:** ${rule}`),
    '',
    "### Don'ts",
    '',
    markdownSection(standards, 'Current Deprecated Patterns'),
    '',
    ...mustNot.map(({ id, rule }) => `- **${id}:** ${rule}`),
  ].join('\n')
}

function generatedDesign() {
  const css = fs.readFileSync(INDEX_CSS, 'utf8')
  const standards = fs.readFileSync(STANDARDS, 'utf8')
  const theme = themeDeclarations(css)
  const tailwind = cssDeclarations(fs.readFileSync(TAILWIND_THEME, 'utf8'))
  const components = componentFiles().map((file) => componentMetadata(file))

  return [
    yamlFrontMatter(theme, tailwind, components),
    '',
    '<!-- Generated by scripts/generate-design-md.mjs. Do not edit by hand. -->',
    '',
    '## Overview',
    '',
    opening(standards),
    '',
    '## Colors',
    '',
    markdownSection(standards, 'Colors'),
    '',
    '## Typography',
    '',
    typographySection(theme, tailwind, css),
    '',
    '## Layout',
    '',
    markdownSection(standards, 'Layout'),
    '',
    '## Elevation & Depth',
    '',
    elevationSection(theme),
    '',
    '## Shapes',
    '',
    shapesSection(theme, standards),
    '',
    '## Components',
    '',
    componentsSection(components),
    '',
    "## Do's and Don'ts",
    '',
    dosAndDontsSection(standards, components),
    '',
  ].join('\n')
}

function diffSummary(actual, expected) {
  const left = actual.split('\n')
  const right = expected.split('\n')
  const changes = []
  const length = Math.max(left.length, right.length)

  for (let index = 0; index < length; index += 1) {
    if (left[index] === right[index]) continue
    if (left[index] !== undefined) changes.push(`- ${left[index]}`)
    if (right[index] !== undefined) changes.push(`+ ${right[index]}`)
    if (changes.length >= 20) {
      changes.push('...')
      break
    }
  }
  return changes.join('\n')
}

const expected = generatedDesign()

if (args[0] === '--check') {
  const actual = fs.existsSync(OUTPUT) ? fs.readFileSync(OUTPUT, 'utf8') : ''
  if (actual !== expected) {
    console.error('Generated DESIGN.md is stale:')
    console.error(diffSummary(actual, expected))
    console.error('\nRun `npm run docs:design` to regenerate it.')
    process.exit(1)
  }
  console.log('DESIGN.md is up to date.')
} else {
  fs.writeFileSync(OUTPUT, expected)
  console.log('Generated DESIGN.md.')
}
