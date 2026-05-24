/**
 * Tests for REQ-9 (Finding B-9): ActivityTab source URL scheme allowlist.
 *
 * SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-9.
 *
 * An LLM-controlled source URL in a widget conversation could contain a
 * `javascript:` URI. React 18+ still navigates on javascript: hrefs, so
 * an admin reviewing a conversation in the ActivityTab drawer could execute
 * attacker-controlled JS in the admin session on my.getklai.com (CC-2).
 *
 * The fix: ActivityTab renders `<a href>` only for http: and https: URLs.
 * All other schemes fall back to plain text (no href).
 *
 * These tests drive the `_isSafeHttpUrl` pure utility exported from
 * ActivityTab.tsx. No DOM rendering required.
 *
 * @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-9
 */
import { describe, it, expect } from 'vitest'
// Import the utility before it exists — this will fail in RED phase.
import { _isSafeHttpUrl } from '../ActivityTab'

describe('_isSafeHttpUrl — REQ-9 scheme allowlist', () => {
  describe('allowed schemes (must return true)', () => {
    it('accepts https:// URLs', () => {
      expect(_isSafeHttpUrl('https://example.com')).toBe(true)
    })

    it('accepts http:// URLs', () => {
      expect(_isSafeHttpUrl('http://example.com')).toBe(true)
    })

    it('accepts https:// with path and query', () => {
      expect(
        _isSafeHttpUrl('https://docs.example.com/article?id=42&lang=nl'),
      ).toBe(true)
    })

    it('accepts https:// with fragment', () => {
      expect(_isSafeHttpUrl('https://example.com/page#section')).toBe(true)
    })
  })

  describe('blocked schemes (must return false)', () => {
    it('blocks javascript: (XSS exploit chain CC-2)', () => {
      expect(_isSafeHttpUrl('javascript:alert(document.cookie)')).toBe(false)
    })

    it('blocks javascript: with encoded colon', () => {
      // Some encodings that a naive startsWith check would miss
      expect(_isSafeHttpUrl('javascript%3Aalert(1)')).toBe(false)
    })

    it('blocks data: URIs', () => {
      expect(_isSafeHttpUrl('data:text/html,<script>alert(1)</script>')).toBe(
        false,
      )
    })

    it('blocks vbscript: URIs', () => {
      expect(_isSafeHttpUrl('vbscript:MsgBox(1)')).toBe(false)
    })

    it('blocks file: URIs', () => {
      expect(_isSafeHttpUrl('file:///etc/passwd')).toBe(false)
    })

    it('blocks mailto: URIs', () => {
      expect(_isSafeHttpUrl('mailto:admin@example.com')).toBe(false)
    })

    it('blocks tel: URIs', () => {
      expect(_isSafeHttpUrl('tel:+31612345678')).toBe(false)
    })

    it('blocks scheme-less paths', () => {
      expect(_isSafeHttpUrl('//example.com/path')).toBe(false)
    })

    it('blocks relative paths', () => {
      expect(_isSafeHttpUrl('/admin/secret')).toBe(false)
    })

    it('blocks empty string', () => {
      expect(_isSafeHttpUrl('')).toBe(false)
    })

    it('blocks whitespace-only string', () => {
      expect(_isSafeHttpUrl('   ')).toBe(false)
    })

    it('blocks javascript: with leading whitespace (bypass attempt)', () => {
      // Some parsers strip leading whitespace before scheme check
      expect(_isSafeHttpUrl('  javascript:alert(1)')).toBe(false)
    })

    it('blocks JAVASCRIPT: (case-insensitive bypass attempt)', () => {
      expect(_isSafeHttpUrl('JAVASCRIPT:alert(1)')).toBe(false)
    })

    it('blocks JaVaScRiPt: (mixed-case bypass attempt)', () => {
      expect(_isSafeHttpUrl('JaVaScRiPt:alert(1)')).toBe(false)
    })
  })
})
