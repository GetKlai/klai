/**
 * @purpose Labeled form-field composition with automatic control association and feedback
 * @guideline KLAI-UI-034 must Every form field has a `Label` with matching id
 * and htmlFor
 */
import * as React from 'react'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

type FieldControlProps = Pick<
  React.HTMLAttributes<HTMLElement>,
  'id' | 'aria-describedby' | 'aria-invalid'
>

export interface FieldProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, 'children'> {
  label: React.ReactNode
  hint?: React.ReactNode
  error?: React.ReactNode
  children: React.ReactElement<FieldControlProps>
}

function Field({
  id,
  label,
  hint,
  error,
  children,
  className,
  ...props
}: FieldProps) {
  const generatedId = React.useId()
  const controlId = id ?? children.props.id ?? generatedId
  const feedback = error ?? hint
  const feedbackId = feedback ? `${controlId}-description` : undefined
  const describedBy = [children.props['aria-describedby'], feedbackId]
    .filter(Boolean)
    .join(' ') || undefined

  const control = React.cloneElement(children, {
    id: controlId,
    'aria-describedby': describedBy,
    'aria-invalid': error ? true : children.props['aria-invalid'],
  })

  return (
    <div className={cn('space-y-1', className)} {...props}>
      <Label htmlFor={controlId}>{label}</Label>
      {control}
      {feedback && (
        <p
          id={feedbackId}
          role={error ? 'alert' : undefined}
          className={cn(
            'text-xs',
            error ? 'text-[var(--color-destructive-text)]' : 'text-gray-600',
          )}
        >
          {feedback}
        </p>
      )}
    </div>
  )
}

export { Field }
