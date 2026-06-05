import { Button, type ButtonProps } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export type InlineRowButtonTone = 'success' | 'destructive' | 'neutral'

/**
 * Tone overrides on top of the ghost base. Filled tones drop the ghost border
 * and use opacity-on-hover (the established inline-pill convention) instead of
 * the ghost background swap.
 */
const TONE: Record<InlineRowButtonTone, string> = {
  success:
    'border-transparent bg-[var(--color-success)] text-white hover:bg-[var(--color-success)] hover:opacity-70',
  destructive:
    'border-transparent bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)] hover:opacity-70',
  neutral: '',
}

export interface InlineRowButtonProps extends Omit<ButtonProps, 'variant' | 'size'> {
  tone?: InlineRowButtonTone
}

/**
 * The single source of truth for small inline-row action pills: Save / Cancel
 * on an inline edit, Delete / Cancel on an inline delete-confirm, Approve /
 * Deny on a request row, etc.
 *
 * Before this component the exact same `h-6 … px-2 gap-1` Button was copy-pasted
 * across 8+ sites (InlineEditRow, InlineDeleteConfirm, SourceRow rename,
 * TranscriptionTable, join-requests, CoverageNodeRow), which let the text size
 * drift (text-[10px] vs text-xs). Everything now flows through here so the size
 * lives in ONE place. Standard size: `h-6 text-xs` with `size-3` icons.
 *
 * Pass the icon + label as children; the caller owns the spinner-vs-icon swap.
 */
export function InlineRowButton({ tone = 'neutral', className, ...props }: InlineRowButtonProps) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className={cn('h-6 gap-1 px-2 text-xs [&_svg]:size-3', TONE[tone], className)}
      {...props}
    />
  )
}
