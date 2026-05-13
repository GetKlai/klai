import { ShieldCheck } from 'lucide-react'
import * as m from '@/paraglide/messages'

export function MfaMethodCard({
  icon,
  title,
  description,
  recommended,
  selected,
  onClick,
}: {
  icon: React.ReactNode
  title: string
  description: string
  recommended?: boolean
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-xl border-2 p-4 text-left transition-all
        ${selected
          ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent)]/5'
          : 'border-gray-200 bg-[var(--color-background)] hover:border-[var(--color-rl-accent)]/50'
        }`}
    >
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 shrink-0 ${selected ? 'text-[var(--color-rl-accent)]' : 'text-[var(--color-rl-cream)]'}`}>
          {icon}
        </div>
        <div className="flex-1 space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-900">{title}</span>
            {recommended && (
              <span className="rounded-full bg-gray-900 px-2 py-0.5 text-xs font-semibold text-white">
                {m.setup_mfa_badge_recommended()}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-400">{description}</p>
        </div>
        {selected && (
          <div className="mt-0.5 shrink-0 text-[var(--color-rl-accent)]">
            <ShieldCheck size={16} />
          </div>
        )}
      </div>
    </button>
  )
}
