import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, formatValidationIssues, type ValidationIssue } from '../apiFetch'

describe('formatValidationIssues', () => {
  it('joins field + message per issue and strips the body. prefix', () => {
    const issues: ValidationIssue[] = [
      {
        loc: ['body', 'email'],
        msg: 'value is not a valid email address',
        type: 'value_error',
      },
      {
        loc: ['body', 'first_name'],
        msg: 'String should have at least 1 character',
        type: 'string_too_short',
      },
    ]
    expect(formatValidationIssues(issues)).toBe(
      'email: value is not a valid email address; first_name: String should have at least 1 character',
    )
  })

  it('returns a bare message when there is no meaningful field path', () => {
    const issues: ValidationIssue[] = [
      { loc: ['body'], msg: 'Request body is required', type: 'missing' },
    ]
    expect(formatValidationIssues(issues)).toBe('Request body is required')
  })
})

describe('ApiError', () => {
  it('builds a status:detail message when no validation issues are present', () => {
    const err = new ApiError(409, 'Group name already exists in this organisation')
    expect(err.message).toBe('409: Group name already exists in this organisation')
    expect(err.detail).toBe('Group name already exists in this organisation')
    expect(err.validationIssues).toBeUndefined()
  })

  it('parses 422 validation issues into a human message and keeps the structured list', () => {
    const issues: ValidationIssue[] = [
      { loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' },
    ]
    const err = new ApiError(422, JSON.stringify(issues), issues)
    expect(err.status).toBe(422)
    expect(err.message).toBe('email: value is not a valid email address')
    expect(err.validationIssues).toEqual(issues)
  })
})

describe('apiFetch — detail body handling', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('stringifies an object detail so callers can JSON.parse(err.detail) to read the error_code', async () => {
    // Portal-api emits this shape for quota / capability / structured errors:
    //   HTTPException(status_code=403, detail={"error_code": "kb_quota_items_exceeded", ...})
    const body = {
      detail: { error_code: 'kb_quota_items_exceeded', plan: 'core', limit: 20, current: 20 },
    }
    // Factory: each call gets a fresh Response (bodies are single-use).
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Response(JSON.stringify(body), {
            status: 403,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    )

    let caught: unknown
    try {
      await apiFetch<unknown>('/api/app/knowledge-bases/personal/sources/text', {
        method: 'POST',
        body: JSON.stringify({ title: 't', content: 'x' }),
      })
    } catch (err) {
      caught = err
    }

    expect(caught).toBeInstanceOf(ApiError)
    const apiErr = caught as ApiError
    expect(apiErr.status).toBe(403)
    // Must be JSON-parseable back into the structured detail.
    const parsed = JSON.parse(apiErr.detail) as { error_code?: string }
    expect(parsed.error_code).toBe('kb_quota_items_exceeded')
  })

  it('keeps a string detail as-is (regression guard)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Response(JSON.stringify({ detail: 'Knowledge base not found' }), {
            status: 404,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    )

    let caught: unknown
    try {
      await apiFetch<unknown>('/api/app/knowledge-bases/missing/sources/text', {
        method: 'POST',
      })
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).detail).toBe('Knowledge base not found')
  })

  it('keeps a validation-issue array as validationIssues (regression guard)', async () => {
    const issues: ValidationIssue[] = [
      { loc: ['body', 'url'], msg: 'field required', type: 'missing' },
    ]
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Response(JSON.stringify({ detail: issues }), {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    )

    let caught: unknown
    try {
      await apiFetch<unknown>('/api/app/knowledge-bases/personal/sources/url', {
        method: 'POST',
        body: JSON.stringify({}),
      })
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).validationIssues).toEqual(issues)
  })
})
