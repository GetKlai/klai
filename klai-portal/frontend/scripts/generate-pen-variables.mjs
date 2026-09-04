import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
)
const INDEX_CSS = path.join(FRONTEND_ROOT, 'src', 'index.css')
const DESIGN_DIR = path.join(FRONTEND_ROOT, 'design')
// The library file is the only one the generator bootstraps; every other
// design/**/*.pen file (each carries its own variables map because pen.dev
// has no cross-file variable import) is only ever updated in place.
const PEN_LIBRARY_FILE = path.join(DESIGN_DIR, 'klai.lib.pen')
const TAILWIND_THEME = fileURLToPath(import.meta.resolve('tailwindcss/theme.css'))

const TW_GRAY_STEPS = ['50', '100', '200', '600', '700', '800', '900']

// pen.dev has no fill opacity, so every Tailwind opacity modifier on a token
// colour (bg-[var(--color-success)]/10, bg-[var(--color-foreground)]/[0.06],
// border-[var(--color-destructive)]/30, ...) must exist as its own tint
// variable. The set of tints is derived from real usage under src/ rather
// than a hardcoded list, so the design library cannot drift from what the
// portal actually renders. Tailwind v4 compiles `bg-[var(--x)]/10` to
// color-mix(in oklab, var(--x) 10%, transparent), which multiplies any alpha
// the base colour already carries; the tint maths below mirrors that. The
// bracketed modifier is a fraction of 1 and is converted to a percentage.
const SRC_DIR = path.join(FRONTEND_ROOT, 'src')
const OPACITY_MODIFIER_PATTERN = /var\(--color-([\w-]+)\)\]\/(?:\[([\d.]+)\]|(\d+))/g

// Canvas-only font substitutes: pen.dev can only render Google Fonts, and the
// brand faces are self-hosted, so canvas text currently falls back to an
// arbitrary system font. These families were metric-matched to the real faces
// (x-height, cap-height, advance widths) so text occupies realistic space on
// the canvas. This map is deliberately NOT derived from src/index.css — it is
// rendering shorthand for the designer, never a source of truth. The truthful
// font-* variables generated from @theme stay untouched; the drift guard in
// generateVariables() only guarantees that every substituted token still
// exists in CSS. Parabole's three weights map to one Schibsted Grotesk family
// because weight is a separate property in pen (fontWeight).
const PREVIEW_FONTS = {
  'font-sans': 'Schibsted Grotesk',
  'font-display': 'Schibsted Grotesk',
  'font-display-bold': 'Schibsted Grotesk',
  'font-mono': 'DM Mono',
}

const args = process.argv.slice(2)
if (args.some((arg) => arg !== '--check') || args.length > 1) {
  console.error('Usage: node scripts/generate-pen-variables.mjs [--check]')
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

function stripBlockComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '')
}

function themeDeclarations(source, warnings) {
  // Tailwind v4 allows more than one @theme / @theme inline block; reading
  // only the first would silently drop tokens, and --check could never see
  // the gap because it compares generated-to-generated.
  const blocks = [...source.matchAll(/@theme(?:\s+inline)?\s*\{/g)]
  if (blocks.length === 0) {
    throw new Error('src/index.css is missing its @theme block')
  }

  // Merge in source order. A Map keeps the position of the first occurrence
  // but takes the value of the last one, which is also how the CSS cascade
  // resolves a duplicate custom property.
  const byName = new Map()
  for (const block of blocks) {
    const open = block.index + block[0].lastIndexOf('{')
    const close = findMatching(source, open, '{', '}')
    const body = stripBlockComments(source.slice(open + 1, close))
    for (const match of body.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
      const decl = {
        name: match[1],
        value: match[2].trim(),
        line: match[0].trim(),
      }
      const previous = byName.get(decl.name)
      if (previous !== undefined && previous.value !== decl.value) {
        warnings.push(
          `src/index.css declares --${decl.name} more than once with different values ` +
            `(${previous.value}, then ${decl.value}); the last declaration wins`,
        )
      }
      byName.set(decl.name, decl)
    }
  }

  return [...byName.values()]
}

function cssDeclarations(source) {
  return [
    ...stripBlockComments(source).matchAll(/--([\w-]+)\s*:\s*([^;]+);/g),
  ].map((match) => ({ name: match[1], value: match[2].trim() }))
}

function round2(value) {
  return Math.round(value * 100) / 100
}

function resolveColorRef(decl, byName) {
  const seen = new Set([decl.name])
  let current = decl.value

  for (;;) {
    const match = /^var\((--[\w-]+)\)$/.exec(current)
    if (!match) return current

    const ref = match[1].slice(2)
    if (seen.has(ref)) {
      throw new Error(
        `src/index.css @theme declaration "${decl.line}" has a circular var() reference via --${ref}`,
      )
    }
    seen.add(ref)

    const target = byName.get(ref)
    if (target === undefined) {
      throw new Error(
        `src/index.css @theme declaration "${decl.line}" references --${ref}, ` +
          'which is not declared in the same @theme block',
      )
    }
    current = target
  }
}

function firstFontFamily(decl) {
  const family = decl.value.split(',')[0].trim()
  const stripped = family.replace(/^(['"])(.*)\1$/, '$2').trim()
  if (!stripped) {
    throw new Error(`src/index.css @theme declaration "${decl.line}" has an empty font family`)
  }
  return stripped
}

function lengthToPx(decl, rootPx) {
  const rem = /^([\d.]+)rem$/.exec(decl.value)
  if (rem) return round2(parseFloat(rem[1]) * rootPx)

  const px = /^([\d.]+)px$/.exec(decl.value)
  if (px) return round2(parseFloat(px[1]))

  throw new Error(
    `src/index.css @theme declaration "${decl.line}" cannot be converted to pixels; ` +
      'expected a rem or px length',
  )
}

function rootFontPx(css, warnings) {
  const match = /html\s*\{[^}]*?font-size:\s*([\d.]+)%\s*;/.exec(css)
  if (!match) {
    warnings.push('src/index.css has no html { font-size: <n>% } rule; using a 16px root')
    return { rootPx: 16, label: '16px (fallback, no html font-size percentage found)' }
  }

  const pct = parseFloat(match[1])
  const rootPx = round2((16 * pct) / 100)
  return { rootPx, label: `${rootPx}px (html { font-size: ${pct}% })` }
}

function oklchToHex(raw, source, tokenName) {
  const match = /^oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s*\)$/.exec(raw.trim())
  if (!match) {
    throw new Error(`${source} declaration --${tokenName}: "${raw}" is not an oklch(L% C H) colour`)
  }

  const l = parseFloat(match[1]) / 100
  const c = parseFloat(match[2])
  const h = (parseFloat(match[3]) * Math.PI) / 180
  const a = c * Math.cos(h)
  const b = c * Math.sin(h)

  const lPrime = l + 0.3963377774 * a + 0.2158037573 * b
  const mPrime = l - 0.1055613458 * a - 0.0638541728 * b
  const sPrime = l - 0.0894841775 * a - 1.291485548 * b

  const lms = [lPrime, mPrime, sPrime].map((channel) => channel ** 3)
  const [lmsL, lmsM, lmsS] = lms
  const linear = [
    4.0767416621 * lmsL - 3.3077115913 * lmsM + 0.2309699292 * lmsS,
    -1.2684380046 * lmsL + 2.6097574011 * lmsM - 0.3413193965 * lmsS,
    -0.0041960863 * lmsL - 0.7034186147 * lmsM + 1.707614701 * lmsS,
  ]

  const to8bit = (channel) => {
    const clamped = Math.min(Math.max(channel, 0), 1)
    const gamma =
      clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055
    return Math.round(Math.min(Math.max(gamma, 0), 1) * 255)
      .toString(16)
      .padStart(2, '0')
  }

  return `#${linear.map(to8bit).join('')}`
}

function tintVariable(name, variable, percentage) {
  const match = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(variable.value)
  if (!match) {
    throw new Error(
      `src/index.css @theme token --${name} is not a hex colour (${variable.value}); ` +
        `cannot derive a ${percentage}% tint`,
    )
  }

  // color-mix multiplies the modifier into the alpha the base already has:
  // #19191899 (60%) tinted at 10% lands near 6%, not 10%.
  const baseAlpha = match[2] === undefined ? 1 : parseInt(match[2], 16) / 255
  const alpha = Math.min(Math.round(((baseAlpha * percentage) / 100) * 255), 255)
    .toString(16)
    .padStart(2, '0')

  return { type: 'color', value: `#${match[1].toLowerCase()}${alpha}` }
}

function findSourceFiles(dir) {
  const found = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const entryPath = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      found.push(...findSourceFiles(entryPath))
    } else if (entry.isFile() && /\.(ts|tsx|css)$/.test(entry.name)) {
      found.push(entryPath)
    }
  }
  return found
}

// Distinct (token, percentage) pairs of Tailwind opacity modifiers on token
// colours, as they appear in class strings: bg-[var(--color-success)]/10 and
// the bracketed fraction form bg-[var(--color-foreground)]/[0.06].
function collectOpacityTints() {
  const pairs = new Map()
  for (const file of findSourceFiles(SRC_DIR).sort()) {
    const rel = path.relative(FRONTEND_ROOT, file)
    const source = fs.readFileSync(file, 'utf8')
    for (const match of source.matchAll(OPACITY_MODIFIER_PATTERN)) {
      const name = match[1]
      const bracketed = match[2] !== undefined
      const modifier = bracketed ? match[2] : match[3]
      const usage = `${rel}: var(--color-${name})]/${bracketed ? `[${modifier}]` : modifier}`
      const raw = Number(modifier)
      if (!Number.isFinite(raw)) {
        throw new Error(`${usage} is not a valid opacity modifier`)
      }
      let percentage = raw
      if (bracketed) {
        if (raw < 0 || raw > 1) {
          throw new Error(
            `${usage} is outside the 0..1 range a bracketed opacity modifier must be in`,
          )
        }
        // /[0.06] is a fraction of 1; the tint variable is named after the
        // percentage (color-foreground-tint-6), so the conversion must land
        // on a whole number. Binary floats make 0.04 * 100 = 4.000000000000001,
        // hence the epsilon.
        const converted = raw * 100
        if (Math.abs(converted - Math.round(converted)) > 1e-9) {
          throw new Error(
            `${usage} converts to ${converted}%; a tint percentage must be a whole number`,
          )
        }
        percentage = Math.round(converted)
      }
      if (percentage < 0 || percentage > 100) {
        throw new Error(
          `${usage} resolves to ${percentage}%; opacity modifiers must resolve to 0-100`,
        )
      }
      pairs.set(`${name}/${percentage}`, { name, percentage })
    }
  }
  return [...pairs.values()].sort(
    (a, b) => a.name.localeCompare(b.name) || a.percentage - b.percentage,
  )
}

function generateVariables() {
  const css = fs.readFileSync(INDEX_CSS, 'utf8')
  const tailwind = new Map(
    cssDeclarations(fs.readFileSync(TAILWIND_THEME, 'utf8')).map(
      (decl) => [decl.name, decl.value],
    ),
  )
  const warnings = []
  const theme = themeDeclarations(css, warnings)
  const byName = new Map(theme.map((decl) => [decl.name, decl.value]))
  const root = rootFontPx(css, warnings)
  const variables = {}

  for (const decl of theme) {
    if (decl.name.startsWith('color-')) {
      variables[decl.name] = {
        type: 'color',
        value: resolveColorRef(decl, byName),
      }
    } else if (decl.name.startsWith('font-')) {
      variables[decl.name] = { type: 'string', value: firstFontFamily(decl) }
    } else if (decl.name.startsWith('radius-')) {
      variables[decl.name] = {
        type: 'number',
        value: lengthToPx(decl, root.rootPx),
      }
    } else {
      warnings.push(
        `skipped @theme token --${decl.name}: not a colour, font, or radius token`,
      )
    }
  }

  for (const step of TW_GRAY_STEPS) {
    const raw = tailwind.get(`color-gray-${step}`)
    if (raw === undefined) {
      throw new Error(`node_modules/tailwindcss/theme.css is missing --color-gray-${step}`)
    }
    variables[`tw-gray-${step}`] = {
      type: 'color',
      value: oklchToHex(raw, 'node_modules/tailwindcss/theme.css', `color-gray-${step}`),
    }
  }

  const spacing = tailwind.get('spacing')
  if (spacing === undefined) {
    throw new Error('node_modules/tailwindcss/theme.css is missing --spacing')
  }
  variables['spacing-unit'] = {
    type: 'number',
    value: lengthToPx({ line: `--spacing: ${spacing};`, value: spacing }, root.rootPx),
  }

  variables['root-font-size'] = { type: 'number', value: root.rootPx }
  variables['color-white'] = { type: 'color', value: '#ffffff' }

  const tints = collectOpacityTints()
  for (const { name, percentage } of tints) {
    const token = `color-${name}`
    if (!byName.has(token)) {
      throw new Error(
        `--${token} is used with an opacity modifier in src/ but is not declared in src/index.css @theme`,
      )
    }
    variables[`${token}-tint-${percentage}`] = tintVariable(
      token,
      variables[token],
      percentage,
    )
  }
  warnings.push(
    `derived ${tints.length} opacity tints from src/ usage across ` +
      `${new Set(tints.map((tint) => tint.name)).size} tokens`,
  )

  for (const [name, substitute] of Object.entries(PREVIEW_FONTS)) {
    if (!byName.has(name)) {
      throw new Error(
        `src/index.css @theme is missing --${name}; cannot emit its canvas preview font`,
      )
    }
    variables[`${name}-preview`] = { type: 'string', value: substitute }
    warnings.push(
      `canvas preview font: ${name} -> ${substitute} (pen.dev cannot load the self-hosted brand face)`,
    )
  }

  warnings.push(`root font size resolved to ${root.label}`)

  // Normalise casing in one final pass: colours copied verbatim from
  // src/index.css keep whatever case the CSS uses (#C0392B), while generated
  // ones (oklch conversions, tints, greys) come out lowercase. Mixed casing
  // in the design files is pure diff noise.
  for (const definition of Object.values(variables)) {
    if (definition.type === 'color') definition.value = definition.value.toLowerCase()
  }

  const sorted = {}
  for (const key of Object.keys(variables).sort()) {
    sorted[key] = variables[key]
  }
  return { variables: sorted, warnings }
}

function findPenFiles(dir) {
  const found = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const entryPath = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      found.push(...findPenFiles(entryPath))
    } else if (entry.isFile() && entry.name.endsWith('.pen')) {
      found.push(entryPath)
    }
  }
  return found
}

function targetPenFiles() {
  const files = fs.existsSync(DESIGN_DIR) ? findPenFiles(DESIGN_DIR) : []
  if (!files.includes(PEN_LIBRARY_FILE)) files.push(PEN_LIBRARY_FILE)
  return files.sort()
}

function readExistingDocument(penFile) {
  if (!fs.existsSync(penFile)) {
    // Empty-shell bootstrap, reached only for the library file: the write
    // path skips every other missing file instead of resurrecting it.
    return { version: '2.17', variables: {}, children: [] }
  }

  const raw = fs.readFileSync(penFile, 'utf8')
  try {
    return JSON.parse(raw)
  } catch {
    throw new Error(
      `${path.relative(FRONTEND_ROOT, penFile)} is not valid JSON; refusing to rewrite it`,
    )
  }
}

// Deletes every `fileToken` key at any depth and returns the JSON paths it
// removed (e.g. ['fileToken', 'children[3].fileToken']). The pen.dev CLI
// writes exactly one top-level token today, but this repo is public and a
// workspace-binding token leaking into git is not something to discover after
// a format change, so the walk is exhaustive. --check runs it on a freshly
// parsed throwaway document and reports the returned paths; the write path
// runs it on the document it is about to serialise.
function stripFileTokens(value, prefix = '', removed = []) {
  if (Array.isArray(value)) {
    value.forEach((item, index) => stripFileTokens(item, `${prefix}[${index}]`, removed))
  } else if (value !== null && typeof value === 'object') {
    for (const key of Object.keys(value)) {
      const childPath = prefix ? `${prefix}.${key}` : key
      if (key === 'fileToken') {
        delete value[key]
        removed.push(childPath)
      } else {
        stripFileTokens(value[key], childPath, removed)
      }
    }
  }
  return removed
}

function serializeDocument(penFile, variables) {
  const document = readExistingDocument(penFile)
  // Assigning to an existing key keeps every other key (version, themes,
  // imports, children, ...) in its current position; only the value changes.
  document.variables = variables

  // fileToken ties the file to a pen.dev cloud workspace and is re-minted
  // locally on save; this repo is public, so it never belongs in git.
  const strippedPaths = stripFileTokens(document)

  return {
    text: JSON.stringify(document, null, 2) + '\n',
    hadFileToken: strippedPaths.length > 0,
  }
}

function variableKeyDiff(actualVariables, expectedVariables) {
  const keys = [
    ...new Set([...Object.keys(actualVariables), ...Object.keys(expectedVariables)]),
  ].sort()
  const changes = []

  for (const key of keys) {
    const actual = actualVariables[key]
    const expected = expectedVariables[key]
    const actualJson = actual === undefined ? undefined : JSON.stringify(actual)
    const expectedJson = expected === undefined ? undefined : JSON.stringify(expected)
    if (actualJson === expectedJson) continue
    if (actualJson !== undefined) changes.push(`- ${key}: ${actualJson}`)
    if (expectedJson !== undefined) changes.push(`+ ${key}: ${expectedJson}`)
    if (changes.length >= 40) {
      changes.push('...')
      break
    }
  }

  return changes
}

const { variables, warnings } = generateVariables()

if (args[0] === '--check') {
  let staleFiles = 0

  for (const penFile of targetPenFiles()) {
    const rel = path.relative(FRONTEND_ROOT, penFile)

    if (!fs.existsSync(penFile)) {
      // Only the library file is guaranteed by the generator; a screen file
      // that does not exist is not drift, deleting it was an explicit choice.
      if (penFile === PEN_LIBRARY_FILE) {
        console.error(`${rel} is missing; run \`node scripts/generate-pen-variables.mjs\` to generate it.`)
        staleFiles += 1
      }
      continue
    }

    let document
    try {
      document = JSON.parse(fs.readFileSync(penFile, 'utf8'))
    } catch {
      console.error(`${rel} is not valid JSON.`)
      staleFiles += 1
      continue
    }

    // Separate check with its own message: a committed fileToken ties this
    // public repo's design file to a pen.dev cloud workspace, and that must
    // fail even when the variables themselves are perfectly up to date. The
    // document was just parsed for the comparison below, which only reads
    // document.variables, so the shared strip helper doubles as the detector
    // and its returned paths say exactly where a token sits.
    const leakedPaths = stripFileTokens(document)
    if (leakedPaths.length > 0) {
      console.error(
        `${rel} has fileToken at ${leakedPaths.join(', ')}, ` +
          'tying it to a pen.dev cloud workspace. ' +
          'This repo is public; run `node scripts/generate-pen-variables.mjs` to strip it.',
      )
      staleFiles += 1
    }

    // Only document.variables is compared: the pen.dev CLI rewrites the file
    // on save (re-orders keys, injects fileToken), so whole-file text
    // comparison would report drift when nothing about the variables actually
    // changed.
    const changes = variableKeyDiff(document.variables ?? {}, variables)
    if (changes.length === 0) continue

    staleFiles += 1
    console.error(`${rel} is stale:`)
    console.error(changes.join('\n'))
  }

  if (staleFiles > 0) {
    console.error('\nRun `node scripts/generate-pen-variables.mjs` to regenerate it.')
    process.exit(1)
  }

  console.log('All design/**/*.pen files are up to date.')
  process.exit(0)
}

const counts = { color: 0, number: 0, string: 0 }
for (const definition of Object.values(variables)) {
  counts[definition.type] += 1
}

for (const penFile of targetPenFiles()) {
  const rel = path.relative(FRONTEND_ROOT, penFile)

  // Discovery only returns files that exist, plus the force-included library
  // constant, so this guard exists to make the rule explicit rather than
  // incidental: a missing screen file was deleted on purpose and must never
  // be recreated as an empty shell by the next run. --check agrees: only
  // design/klai.lib.pen may bootstrap.
  if (!fs.existsSync(penFile) && penFile !== PEN_LIBRARY_FILE) {
    warnings.push(`${rel} does not exist; skipped instead of created`)
    continue
  }

  fs.mkdirSync(path.dirname(penFile), { recursive: true })
  const { text, hadFileToken } = serializeDocument(penFile, variables)
  fs.writeFileSync(penFile, text)

  console.log(
    `${rel}: ${Object.keys(variables).length} variables ` +
      `(${counts.color} color, ${counts.number} number, ${counts.string} string)`,
  )
  if (hadFileToken) {
    console.log(`${rel}: stripped fileToken (public repo)`)
  }
}

console.log('WARNINGS:')
for (const warning of warnings) {
  console.log(`- ${warning}`)
}
