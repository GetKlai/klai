export const SCRIBE_BASE = '/api/scribe/v1'
export const ACCEPTED_TYPES = '.wav,.mp3,.m4a,.ogg,.webm'
export const MAX_UPLOAD_MB = 100

export type AddTranscribeTab = 'record' | 'upload'

export const ADD_TRANSCRIBE_TABS: AddTranscribeTab[] = ['record', 'upload']

export function isAddTranscribeTab(value: unknown): value is AddTranscribeTab {
  return typeof value === 'string' && ADD_TRANSCRIBE_TABS.includes(value as AddTranscribeTab)
}

export function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}
