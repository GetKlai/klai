import { describe, expect, it } from 'vitest'
import { formatDuration, isAddTranscribeTab } from '../-add-helpers'

describe('isAddTranscribeTab', () => {
  it('accepts supported transcribe add tabs', () => {
    expect(isAddTranscribeTab('record')).toBe(true)
    expect(isAddTranscribeTab('upload')).toBe(true)
  })

  it('rejects unknown or non-string values', () => {
    expect(isAddTranscribeTab('paste')).toBe(false)
    expect(isAddTranscribeTab(undefined)).toBe(false)
    expect(isAddTranscribeTab(1)).toBe(false)
  })
})

describe('formatDuration', () => {
  it('formats seconds as m:ss', () => {
    expect(formatDuration(0)).toBe('0:00')
    expect(formatDuration(65)).toBe('1:05')
    expect(formatDuration(3599)).toBe('59:59')
  })
})
