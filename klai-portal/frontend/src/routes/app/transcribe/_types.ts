export type Source = 'upload' | 'meeting'

export interface UnifiedItem {
  id: string
  source: Source
  title: string | null
  text: string | null
  language: string | null
  duration_seconds: number | null
  created_at: string
  status: string
  uploadName?: string | null
  meeting_url?: string
  platform?: string
  has_summary?: boolean
}

export interface TranscriptionItem {
  id: string
  name: string | null
  status: string
  text: string | null
  language: string | null
  duration_seconds: number | null
  created_at: string
  has_summary?: boolean
}

export interface TranscriptionListResponse {
  items: TranscriptionItem[]
  total: number
}

export interface MeetingListItem {
  id: string
  platform: string
  meeting_url: string
  meeting_title: string | null
  status: string
  created_at: string
  duration_seconds: number | null
  transcript_text: string | null
  language: string | null
}

export interface MeetingListResponse {
  items: MeetingListItem[]
  total: number
}
