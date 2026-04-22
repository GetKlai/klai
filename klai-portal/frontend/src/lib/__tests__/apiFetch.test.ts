import { describe, expect, it } from 'vitest'
import { ApiError, formatValidationIssues, type ValidationIssue } from '../apiFetch'

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
