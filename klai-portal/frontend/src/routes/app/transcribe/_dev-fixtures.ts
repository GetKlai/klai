/**
 * Dev-only mock data for when the Scribe/Vexa backends are not running locally.
 * Used only when VITE_AUTH_DEV_MODE is enabled and the real API returns 404.
 */
import type { TranscriptionListResponse, MeetingListResponse } from './_types'

export const DEV_TRANSCRIPTIONS: TranscriptionListResponse = {
  items: [
    {
      id: 'dev-upload-1',
      name: 'Product roadmap Q2',
      status: 'transcribed',
      text: 'We need to focus on three key areas this quarter. First, the knowledge base search quality needs improvement. Second, we should ship the meeting bot integration. Third, the billing flow needs to support annual plans.',
      language: 'en',
      duration_seconds: 2340,
      created_at: '2026-04-09T14:30:00Z',
      has_summary: true,
    },
    {
      id: 'dev-upload-2',
      name: 'Klai Standup',
      status: 'transcribed',
      text: 'Toegevoegd aan de meetweg misschien. Maar goed, het is goed om daarmee te beginnen. Mark, hoe heb jij het weekend gehad? Ja prima, ik heb vooral aan de connector gewerkt.',
      language: 'nl',
      duration_seconds: 624,
      created_at: '2026-04-02T09:00:00Z',
      has_summary: false,
    },
    {
      id: 'dev-upload-3',
      name: null,
      status: 'processing',
      text: null,
      language: null,
      duration_seconds: null,
      created_at: '2026-04-10T08:15:00Z',
    },
    {
      id: 'dev-upload-4',
      name: 'Support call recording',
      status: 'failed',
      text: null,
      language: null,
      duration_seconds: null,
      created_at: '2026-04-08T16:45:00Z',
    },
  ],
  total: 4,
}

export const DEV_MEETINGS: MeetingListResponse = {
  items: [
    {
      id: 'dev-meeting-1',
      platform: 'google_meet',
      meeting_url: 'https://meet.google.com/abc-defg-hij',
      meeting_title: 'Wouter / Mark',
      status: 'completed',
      created_at: '2026-04-09T10:00:00Z',
      duration_seconds: 3420,
      transcript_text: 'Wouter van den Bijgaart: ...snel even aansluiten. Ik vind het pijn om naast mijn collega\'s te werken. Alleen als je aan de andere kant zit dan is het wel fijn.',
      language: 'en',
    },
    {
      id: 'dev-meeting-2',
      platform: 'google_meet',
      meeting_url: 'https://meet.google.com/xyz-uvwx-rst',
      meeting_title: 'Customer Experience',
      status: 'completed',
      created_at: '2026-04-08T13:00:00Z',
      duration_seconds: 2700,
      transcript_text: 'Mark Vletter: a notion page with the metrics we\'re going to talk about then we can go through them one by one.',
      language: 'en',
    },
    {
      id: 'dev-meeting-3',
      platform: 'google_meet',
      meeting_url: 'https://meet.google.com/live-now',
      meeting_title: 'AI Council Weekly',
      status: 'recording',
      created_at: '2026-04-10T10:00:00Z',
      duration_seconds: null,
      transcript_text: null,
      language: null,
    },
  ],
  total: 3,
}
