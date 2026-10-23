/**
 * J06 - The customer widget answers a question from its knowledge base.
 *
 * On 2026-10-23 the widget answered "Er ging iets mis" from 03:00 to 10:40 UTC
 * (a FalkorDB crash loop stalled retrieval) while J01-J05 stayed green,
 * because no journey walked the partner API a customer's browser calls. This
 * one does, with the same two calls as klai-widget/src/api/widget-config.ts
 * and chat-stream.ts (scripts/widget-probe.py does the same in Python on
 * core-01 every five minutes):
 *
 *   1. GET  /partner/v1/widget-config?id=<widget id>  (Origin + session id)
 *        -> session_token + chat_endpoint
 *   2. POST <chat_endpoint> with Bearer <session_token>, stream=true -> SSE
 *
 * It uses plain fetch from the test process, not the page: the widget runs on
 * the customer's origin, not on the portal, and no portal login is involved.
 * The probe widget (`probe-widget` on `probe-widget-kb`, see
 * docs/runbooks/widget-probe.md) holds one document that answers the question.
 *
 * Env:
 *   E2E_PROBE_WIDGET_ID          required: the probe widget's wgt_... id. Not
 *                                set in CI is a failure, not a skip: a
 *                                missing secret must not turn the widget
 *                                check green. Locally it skips.
 *   E2E_PROBE_WIDGET_ORIGIN      an origin in the widget's allowed_origins
 *                                (default https://probe.example.com)
 *   E2E_PROBE_QUESTION           a question the probe KB answers (default:
 *                                the runbook's question)
 *   E2E_PROBE_SOURCE_PATTERN     regex a source title or label must match
 *                                (default `probe`, case-insensitive)
 *   E2E_PROBE_API_BASE_URL       default https://api.getklai.com
 */
import { test, expect } from '@playwright/test'

const WIDGET_ID = process.env.E2E_PROBE_WIDGET_ID
const ORIGIN = process.env.E2E_PROBE_WIDGET_ORIGIN || 'https://probe.example.com'
const QUESTION =
  process.env.E2E_PROBE_QUESTION ||
  'Wat zijn de openingstijden van het supportteam van Probe Bakkerij?'
const SOURCE_PATTERN = new RegExp(process.env.E2E_PROBE_SOURCE_PATTERN || 'probe', 'i')
const API_BASE = (process.env.E2E_PROBE_API_BASE_URL || 'https://api.getklai.com').replace(/\/$/, '')

// Same threshold and reasoning as WIDGET_PROBE_MAX_LATENCY_MS in
// scripts/widget-probe.py: p95 of partner_chat_turn_timing total_ms was 11.9 s
// in the week before the incident.
const MAX_TOTAL_MS = 30_000
// Hard stop for a stalled stream, above MAX_TOTAL_MS so a slow answer is
// reported as slow with its time, and a hang as a hang.
const ABORT_AFTER_MS = 45_000

interface MintResponse {
  session_token: string
  chat_endpoint: string
}

interface StreamSource {
  label?: string
  title?: string
}

function expectCors(res: Response, step: string) {
  // A browser drops a response whose Access-Control-Allow-Origin does not
  // match the page, so a CORS regression breaks every customer widget while
  // the API itself still answers.
  const allow = res.headers.get('access-control-allow-origin')
  expect([ORIGIN, '*'], `${step}: Access-Control-Allow-Origin (got ${allow})`).toContain(allow)
}

test.describe('J06 - customer widget answer', () => {
  test.skip(!WIDGET_ID && !process.env.CI, 'E2E_PROBE_WIDGET_ID is not set (runs in CI only)')

  test('probe widget answers its question from its knowledge base, with a source', async () => {
    if (!WIDGET_ID) {
      throw new Error(
        'E2E_PROBE_WIDGET_ID is not set. J06 needs the probe widget id (wgt_...) from ' +
          'docs/runbooks/widget-probe.md as a GitHub secret passed to the e2e-prod-tenant workflow.',
      )
    }
    const signal = AbortSignal.timeout(ABORT_AFTER_MS)
    const started = Date.now()

    // 1. Mint a session token exactly as the widget does.
    const mintRes = await fetch(
      `${API_BASE}/partner/v1/widget-config?${new URLSearchParams({ id: WIDGET_ID })}`,
      {
        headers: { Origin: ORIGIN, 'X-Klai-Widget-Session-Id': crypto.randomUUID().replaceAll('-', '') },
        signal,
      },
    )
    expect(mintRes.status, 'widget-config mint must answer 2xx').toBeLessThan(300)
    expectCors(mintRes, 'widget-config')
    const mint = (await mintRes.json()) as Partial<MintResponse>
    expect(mint.session_token, 'widget-config must return a session_token').toBeTruthy()
    expect(mint.chat_endpoint, 'widget-config must return a chat_endpoint').toBeTruthy()
    const endpoint = mint.chat_endpoint!.startsWith('/') ? API_BASE + mint.chat_endpoint : mint.chat_endpoint!

    // 2. Ask the question and read the stream to its end.
    const chatRes = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${mint.session_token}`,
        Origin: ORIGIN,
      },
      body: JSON.stringify({ messages: [{ role: 'user', content: QUESTION }], stream: true }),
      signal,
    })
    expect(chatRes.status, 'chat stream must answer 2xx').toBeLessThan(300)
    expectCors(chatRes, 'chat')

    let answer = ''
    const sources: StreamSource[] = []
    let done = false
    let errorFrame: unknown
    const decoder = new TextDecoder()
    let buffered = ''
    const handleLine = (line: string) => {
      if (!line.startsWith('data:')) return
      const data = line.slice('data:'.length).trim()
      if (data === '[DONE]') {
        done = true
        return
      }
      let event: { error?: unknown; choices?: { delta?: { content?: string; sources?: StreamSource[] } }[] }
      try {
        event = JSON.parse(data)
      } catch {
        return
      }
      if (event.error) errorFrame = event.error
      const delta = event.choices?.[0]?.delta
      answer += delta?.content ?? ''
      if (Array.isArray(delta?.sources)) sources.push(...delta.sources)
    }
    const reader = chatRes.body!.getReader()
    for (let r = await reader.read(); !r.done; r = await reader.read()) {
      buffered += decoder.decode(r.value, { stream: true })
      const lines = buffered.split('\n')
      buffered = lines.pop() ?? ''
      lines.forEach((l) => handleLine(l.trim()))
    }
    handleLine(buffered.trim())
    const totalMs = Date.now() - started

    expect(errorFrame, 'stream must carry no error frame').toBeUndefined()
    // Text and sources can already have arrived when the server closes the
    // stream early; only [DONE] proves the answer is complete.
    expect(done, 'stream must end with [DONE]').toBe(true)
    expect(answer.trim().length, 'the answer must not be empty').toBeGreaterThan(0)
    // A fixed "Er ging iets mis" reply is HTTP 200 without sources, so the
    // source check is what catches a retrieval that found nothing.
    expect(
      sources.some((s) => SOURCE_PATTERN.test(`${s.title ?? ''} ${s.label ?? ''}`)),
      `at least one source must refer to the probe document (${SOURCE_PATTERN}); got ${JSON.stringify(sources)}`,
    ).toBe(true)
    expect(totalMs, `mint + answer took ${totalMs} ms`).toBeLessThan(MAX_TOTAL_MS)
  })
})
