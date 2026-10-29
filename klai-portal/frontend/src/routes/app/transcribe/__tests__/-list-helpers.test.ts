import { describe, expect, it } from 'vitest'
import { hasActiveItems } from '../-list-helpers'
import type { MeetingListItem, TranscriptionItem } from '../_types'

function upload(status: string): TranscriptionItem {
  return {
    id: `upload-${status}`,
    name: null,
    status,
    text: null,
    language: null,
    duration_seconds: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function meeting(status: string): MeetingListItem {
  return {
    id: `meeting-${status}`,
    platform: 'google_meet',
    meeting_url: 'https://meet.example.com/abc',
    meeting_title: null,
    status,
    created_at: '2026-01-01T00:00:00Z',
    duration_seconds: null,
    transcript_text: null,
    language: null,
  }
}

describe('hasActiveItems', () => {
  it('is true for an upload that is still processing', () => {
    expect(hasActiveItems([upload('transcribed'), upload('processing')], [])).toBe(true)
  })

  it('is true for an active meeting', () => {
    expect(hasActiveItems([], [meeting('completed'), meeting('recording')])).toBe(true)
  })

  it('is false for finished uploads and completed meetings', () => {
    expect(
      hasActiveItems([upload('transcribed'), upload('failed')], [meeting('completed')]),
    ).toBe(false)
  })

  it('is false for undefined inputs', () => {
    expect(hasActiveItems(undefined, undefined)).toBe(false)
  })
})
