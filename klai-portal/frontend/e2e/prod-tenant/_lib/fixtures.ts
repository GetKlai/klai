/**
 * Test-fixture loaders.
 *
 * Static fixtures live in ../fixtures/. Helpers here resolve their paths
 * and expose the unique canary strings used by RAG / transcribe assertions.
 *
 * docs/testing/test-suite-plan.md §6.
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// ESM-friendly __dirname.
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURES_DIR = path.resolve(__dirname, '..', 'fixtures')

/**
 * Markdown KB fixture used by J03.
 * Contains the canary string `KB_CANARY` exactly once.
 */
export const KB_FIXTURE = {
  path: path.join(FIXTURES_DIR, 'e2e-fixture.md'),
  /** Exact canary string the RAG assertion looks for in the chat response. */
  canary: 'klai-e2e-canary-string-42',
} as const

/**
 * Audio fixture for J06 (scribe upload + transcribe).
 * ~3 seconds, mono 16kHz, contains the spoken phrase `audio canary`.
 */
export const AUDIO_FIXTURE = {
  path: path.join(FIXTURES_DIR, 'e2e-fixture.wav'),
  /** Words the transcribed text MUST contain (case-insensitive substring). */
  expectedTranscriptContains: 'test',
} as const

export const FIXTURE_DIR = FIXTURES_DIR
