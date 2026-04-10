import { useState, useRef, type ReactNode } from 'react'

interface TooltipProps {
  label: string
  children: ReactNode
  className?: string
}

export function Tooltip({ label, children, className }: TooltipProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number; rightAligned: boolean } | null>(null)

  return (
    <div
      ref={ref}
      className={className}
      onMouseEnter={() => {
        const rect = ref.current?.getBoundingClientRect()
        if (rect) {
          const center = rect.left + rect.width / 2
          // Right-align when within 160px of viewport right edge to prevent overflow
          const rightAligned = center > window.innerWidth - 160
          setPos({
            top: rect.top,
            left: rightAligned ? rect.right : center,
            rightAligned,
          })
        }
      }}
      onMouseLeave={() => setPos(null)}
    >
      {children}
      {pos && (
        <div
          style={{
            position: 'fixed',
            top: pos.top - 6,
            left: pos.left,
            transform: pos.rightAligned ? 'translate(-100%, -100%)' : 'translate(-50%, -100%)',
            zIndex: 50,
          }}
          className="px-2 py-1 text-xs text-white bg-[var(--color-foreground)] rounded whitespace-nowrap pointer-events-none"
        >
          {label}
          <div
            style={{
              position: 'absolute',
              top: '100%',
              left: pos.rightAligned ? 'auto' : '50%',
              right: pos.rightAligned ? '8px' : 'auto',
              transform: pos.rightAligned ? 'none' : 'translateX(-50%)',
              width: 0,
              height: 0,
              borderLeft: '5px solid transparent',
              borderRight: '5px solid transparent',
              borderTop: '5px solid var(--color-foreground)',
            }}
          />
        </div>
      )}
    </div>
  )
}
