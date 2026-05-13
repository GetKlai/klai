import { X } from 'lucide-react'
import type { GroupMember, UserMember } from '../-kb-types'

interface GroupMemberRowProps {
  kind: 'group'
  member: GroupMember
  isOwner: boolean
  onRemove: (id: number) => void
}

interface UserMemberRowProps {
  kind: 'user'
  member: UserMember
  isOwner: boolean
  myUserId?: string
  onRemove: (id: number) => void
}

type MemberRowProps = GroupMemberRowProps | UserMemberRowProps

export function MemberRow(props: MemberRowProps) {
  const { isOwner, onRemove } = props
  const canRemove =
    props.kind === 'group' ||
    (props.kind === 'user' && props.member.user_id !== props.myUserId)

  return (
    <div className="flex items-center justify-between rounded-lg border border-gray-200 bg-[var(--color-card)] px-3 py-2">
      {props.kind === 'group' ? (
        <span className="text-sm text-gray-900">{props.member.group_name}</span>
      ) : (
        <div>
          <span className="text-sm text-gray-900">{props.member.display_name ?? props.member.email ?? props.member.user_id}</span>
          {props.member.display_name && props.member.email && (
            <span className="ml-2 text-xs text-gray-400">{props.member.email}</span>
          )}
        </div>
      )}

      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400">{props.member.role}</span>
        {isOwner && canRemove && (
          <button
            type="button"
            onClick={() => onRemove(props.member.id)}
            className="flex h-6 w-6 items-center justify-center text-gray-400 hover:text-[var(--color-destructive)] transition-colors"
            aria-label="Remove member"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}
