/**
 * Async-status polling helper.
 *
 * Many klai journeys involve background work (KB ingestion, scribe
 * transcription, tenant-provisioning). The standard pattern is:
 * trigger the action, then poll an API endpoint until it reports
 * the expected state — or time out.
 *
 * Usage:
 *   const result = await pollUntil({
 *     fn: async () => {
 *       const r = await page.request.get('/api/knowledge/bases/xyz/status')
 *       return await r.json()
 *     },
 *     until: (state) => state.status === 'ready',
 *     timeoutMs: 90_000,
 *     intervalMs: 2_000,
 *     description: 'KB ingestion',
 *   })
 *
 * docs/testing/test-suite-plan.md §4.
 */

export interface PollOptions<T> {
  /** Function called every interval. Should return the current state. */
  fn: () => Promise<T>
  /** Predicate: when true, polling stops and the value is returned. */
  until: (value: T) => boolean
  /** Max total wait time. Default 60s. */
  timeoutMs?: number
  /** Time between polls. Default 1s. */
  intervalMs?: number
  /** Human-readable description for the timeout error message. */
  description?: string
}

/**
 * Poll fn() at intervalMs until until() returns true OR timeoutMs is hit.
 * Returns the value that satisfied until(); throws on timeout.
 */
export async function pollUntil<T>(opts: PollOptions<T>): Promise<T> {
  const timeoutMs = opts.timeoutMs ?? 60_000
  const intervalMs = opts.intervalMs ?? 1_000
  const description = opts.description ?? 'condition'

  const start = Date.now()
  let lastValue: T | undefined

  while (Date.now() - start < timeoutMs) {
    try {
      lastValue = await opts.fn()
      if (opts.until(lastValue)) {
        return lastValue
      }
    } catch (err) {
      // Swallow transient errors; the next iteration will retry.
      // If the error persists past timeout, the throw at the end fires.
      // Useful for endpoints that 5xx briefly during ingestion start-up.
    }
    await sleep(intervalMs)
  }

  throw new Error(
    `pollUntil: ${description} did not satisfy predicate within ${timeoutMs}ms. ` +
      `Last value: ${JSON.stringify(lastValue)}`,
  )
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
