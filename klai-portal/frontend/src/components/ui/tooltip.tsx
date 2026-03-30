import { useState, useRef, type ReactNode } from 'react'

interface TooltipProps {
  label: string
  children: ReactNode
}

export function Tooltip({ label, children }: TooltipProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  return (
    <div
      ref={ref}
      onMouseEnter={() => {
        const rect = ref.current?.getBoundingClientRect()
        if (rect) setPos({ top: rect.top, left: rect.left + rect.width / 2 })
      }}
      onMouseLeave={() => setPos(null)}
    >
      {children}
      {pos && (
        <div
          style={{ position: 'fixed', top: pos.top - 6, left: pos.left, transform: 'translate(-50%, -100%)', zIndex: 50 }}
          className="px-2 py-1 text-xs text-white bg-[var(--color-purple-deep)] rounded whitespace-nowrap pointer-events-none"
        >
          {label}
          <div style={{ position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)', width: 0, height: 0, borderLeft: '5px solid transparent', borderRight: '5px solid transparent', borderTop: '5px solid var(--color-purple-deep)' }} />
        </div>
      )}
    </div>
  )
}
