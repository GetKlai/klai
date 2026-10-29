import type { MeetingListItem, TranscriptionItem } from './_types'

const ACTIVE_MEETING_STATUSES = ['pending', 'joining', 'recording', 'stopping', 'processing']

export function hasActiveItems(
  transcriptions: TranscriptionItem[] | undefined,
  meetings: MeetingListItem[] | undefined,
): boolean {
  return (
    (transcriptions ?? []).some((item) => item.status === 'processing') ||
    (meetings ?? []).some((mtg) => ACTIVE_MEETING_STATUSES.includes(mtg.status))
  )
}
