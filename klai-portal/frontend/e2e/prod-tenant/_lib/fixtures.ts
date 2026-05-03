/**
 * Test-fixture loaders + naming convention for test-created artifacts.
 *
 * Static fixtures live in ../fixtures/. Helpers here resolve their paths
 * and expose the unique canary strings used by RAG / transcribe assertions.
 *
 * Every artifact created by an e2e journey (KB, template, transcript,
 * etc.) MUST be prefixed with `e2ePrefix()` so that:
 *   - cleanup helpers can scope their deletes to test-created items only
 *     (critical when running against a real tenant like Voys; never touch
 *     genuine user data),
 *   - parallel/sequential test-runs don't collide on identical names.
 *
 * docs/testing/test-suite-plan.md §6 + §11.
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// ESM-friendly __dirname.
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURES_DIR = path.resolve(__dirname, '..', 'fixtures')

/**
 * Returns the canonical e2e-naming prefix for THIS test run.
 *
 * Format: `e2e-<unix-ms>-`
 * Example: `e2e-1762345678901-my-test-kb`
 *
 * The prefix is captured once per process, so all artifacts created by
 * the same Playwright run share it — making bulk cleanup via "delete
 * everything starting with e2e-<run-ts>-" trivially scoped.
 *
 * Cleanup helpers (`_lib/cleanup.ts`) refuse to delete anything that
 * does NOT start with `e2e-` — defence-in-depth against accidentally
 * wiping real tenant data when running in voys-attached mode.
 */
let _runPrefix: string | null = null
export function e2ePrefix(): string {
  if (_runPrefix === null) {
    _runPrefix = `e2e-${Date.now()}-`
  }
  return _runPrefix
}

/**
 * The hard guard: any name handled by cleanup helpers MUST start with
 * this. Don't change without updating cleanup.ts in lockstep.
 */
export const E2E_NAME_GUARD = 'e2e-'

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
