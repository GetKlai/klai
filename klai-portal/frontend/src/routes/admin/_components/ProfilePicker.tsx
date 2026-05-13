import { useId } from 'react'
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'
import * as m from '@/paraglide/messages'

interface ProfilePickerProps {
  value: ProfileRole | ''
  onChange: (role: ProfileRole) => void
  disabled?: boolean
  disabledMessage?: string
  /**
   * Compact rendering: smaller padding, no description text.
   * Used inside dropdown menus or tight forms.
   */
  compact?: boolean
}

/**
 * Shared radio-card profile picker for the 5-rung profile ladder
 * (SPEC-PORTAL-PROFILES-001). Controlled component — caller owns state and
 * persistence.
 *
 * Visual rules per portal-patterns.md v1-spine:
 * - border-gray-200 unselected, border-gray-900 selected (no amber accent —
 *   "Amber NOT applied to active states in v1")
 * - bg-black/[0.06] selected layer, hover bg-black/5
 * - text-gray-900 prose, text-gray-400 muted
 */
export function ProfilePicker({
  value,
  onChange,
  disabled = false,
  disabledMessage,
  compact = false,
}: ProfilePickerProps) {
  const msgs = m as unknown as Record<string, (() => string) | undefined>
  // Unique radio-group name so multiple <ProfilePicker> instances on the
  // same page don't share selection state.
  const radioGroupName = useId()

  return (
    <div className="space-y-3">
      <div className={compact ? 'space-y-1.5' : 'space-y-2'} role="radiogroup" aria-label={m.profile_picker_title()}>
        {PROFILE_LADDER.map((role) => {
          const labelFn = msgs[`profile_${role}_label`]
          const descFn = msgs[`profile_${role}_description`]
          const isSelected = value === role
          return (
            <label
              key={role}
              className={[
                'flex items-start gap-3 rounded-lg border cursor-pointer transition-colors',
                compact ? 'p-2' : 'p-3',
                isSelected
                  ? 'border-gray-900 bg-black/[0.06]'
                  : 'border-gray-200 hover:bg-black/5',
                disabled ? 'opacity-50 cursor-not-allowed' : '',
              ].join(' ')}
            >
              <input
                type="radio"
                name={radioGroupName}
                value={role}
                checked={isSelected}
                disabled={disabled}
                onChange={() => {
                  if (!disabled) onChange(role)
                }}
                className="mt-0.5 accent-gray-900"
              />
              <div className="space-y-0.5">
                <p className="text-sm font-medium text-gray-900">
                  {labelFn ? labelFn() : role}
                </p>
                {!compact && (
                  <p className="text-xs text-gray-400">
                    {descFn ? descFn() : ''}
                  </p>
                )}
              </div>
            </label>
          )
        })}
      </div>
      {disabled && disabledMessage && (
        <p className="text-xs text-gray-400">{disabledMessage}</p>
      )}
    </div>
  )
}
