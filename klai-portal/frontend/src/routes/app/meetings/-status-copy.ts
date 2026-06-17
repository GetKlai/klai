export type ActiveMeetingInfoKind = 'active' | 'processing' | 'stopping'

export function activeMeetingInfoKind(status: string): ActiveMeetingInfoKind {
  if (status === 'stopping') return 'stopping'
  if (status === 'processing') return 'processing'
  return 'active'
}
