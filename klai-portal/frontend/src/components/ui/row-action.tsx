import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  ExternalLink,
  Eye,
  Info,
  Link as LinkIcon,
  Loader2,
  LogOut,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Settings,
  Square,
  Trash2,
  Upload,
  UserX,
  X,
} from 'lucide-react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'
import { Tooltip } from '@/components/ui/tooltip'

const rowActionToneByAction = {
  add: 'primary',
  edit: 'warning',
  rename: 'neutral',
  configure: 'neutral',
  open: 'neutral',
  external: 'neutral',
  sync: 'success',
  retry: 'warning',
  copy: 'neutral',
  reauth: 'neutral',
  send: 'primary',
  view: 'neutral',
  download: 'neutral',
  upload: 'neutral',
  search: 'neutral',
  save: 'success',
  delete: 'danger',
  stop: 'danger',
  cancel: 'neutral',
  more: 'neutral',
  expand: 'neutral',
  collapse: 'neutral',
  suspend: 'warning',
  reactivate: 'success',
  leave: 'danger',
  offboard: 'danger',
  info: 'info',
} as const

const rowActionIcons = {
  add: Plus,
  edit: Pencil,
  rename: Pencil,
  configure: Settings,
  open: ExternalLink,
  external: ExternalLink,
  sync: RefreshCw,
  retry: RotateCcw,
  copy: Copy,
  reauth: LinkIcon,
  send: Send,
  view: Eye,
  download: Download,
  upload: Upload,
  search: Search,
  save: Check,
  delete: Trash2,
  stop: Square,
  cancel: X,
  more: MoreHorizontal,
  expand: ChevronRight,
  collapse: ChevronDown,
  suspend: Square,
  reactivate: Play,
  leave: LogOut,
  offboard: UserX,
  info: Info,
} as const

export type RowActionKind = keyof typeof rowActionIcons
export type RowActionTone = (typeof rowActionToneByAction)[RowActionKind]
export type RowActionIcon = React.ComponentType<{ className?: string }>

const rowActionIconButtonVariants = cva(
  'inline-flex h-8 w-8 items-center justify-center rounded-md transition-opacity hover:opacity-70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:h-4 [&_svg]:w-4 [&_svg]:shrink-0',
  {
    variants: {
      tone: {
        neutral: 'text-gray-400',
        primary: 'text-[var(--color-primary)]',
        info: 'text-[var(--color-info-text)]',
        danger: 'text-[var(--color-destructive)]',
        success: 'text-[var(--color-success)]',
        warning: 'text-[var(--color-warning)]',
      },
    },
    defaultVariants: {
      tone: 'neutral',
    },
  },
)

const rowActionButtonVariants = cva(
  'inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-2 text-xs font-medium transition-opacity hover:opacity-70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:h-3.5 [&_svg]:w-3.5 [&_svg]:shrink-0',
  {
    variants: {
      tone: {
        neutral: 'text-gray-500',
        primary: 'text-[var(--color-primary)]',
        info: 'text-[var(--color-info-text)]',
        danger: 'text-[var(--color-destructive)]',
        success: 'text-[var(--color-success)]',
        warning: 'text-[var(--color-warning)]',
      },
    },
    defaultVariants: {
      tone: 'neutral',
    },
  },
)

function toneForAction(action?: RowActionKind, tone?: RowActionTone | null): RowActionTone {
  return tone ?? (action ? rowActionToneByAction[action] : 'neutral')
}

function iconForAction(action?: RowActionKind, icon?: RowActionIcon): RowActionIcon | undefined {
  return icon ?? (action ? rowActionIcons[action] : undefined)
}

function renderActionIcon(action?: RowActionKind, icon?: RowActionIcon) {
  const actionIcon = iconForAction(action, icon)
  return actionIcon ? React.createElement(actionIcon) : null
}

interface RowActionTooltipProps {
  label: string
  tooltip?: boolean
  children: React.ReactNode
}

function RowActionTooltip({ label, tooltip = true, children }: RowActionTooltipProps) {
  if (!tooltip) return children
  return <Tooltip label={label}>{children}</Tooltip>
}

export interface RowActionIconButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof rowActionIconButtonVariants> {
  label: string
  action?: RowActionKind
  icon?: RowActionIcon
  asChild?: boolean
  tooltip?: boolean
  spinner?: React.ReactNode
}

const RowActionIconButton = React.forwardRef<HTMLButtonElement, RowActionIconButtonProps>(
  (
    {
      label,
      action,
      icon,
      tone,
      asChild = false,
      tooltip = true,
      spinner,
      className,
      children,
      type = 'button',
      ...props
    },
    ref,
  ) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <RowActionTooltip label={label} tooltip={tooltip}>
        <Comp
          ref={ref}
          type={asChild ? undefined : type}
          aria-label={label}
          className={cn(rowActionIconButtonVariants({ tone: toneForAction(action, tone) }), className)}
          {...props}
        >
          {spinner ?? children ?? renderActionIcon(action, icon)}
        </Comp>
      </RowActionTooltip>
    )
  },
)
RowActionIconButton.displayName = 'RowActionIconButton'

const BorderedRowActionIconButton = React.forwardRef<HTMLButtonElement, RowActionIconButtonProps>(
  ({ className, ...props }, ref) => (
    <RowActionIconButton
      ref={ref}
      className={cn(
        'h-8 w-8 border border-gray-200 bg-white hover:border-current hover:bg-[var(--color-hover)] [&_svg]:h-3.5 [&_svg]:w-3.5',
        className,
      )}
      {...props}
    />
  ),
)
BorderedRowActionIconButton.displayName = 'BorderedRowActionIconButton'

export interface RowActionButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof rowActionButtonVariants> {
  label: string
  action?: RowActionKind
  icon?: RowActionIcon
  asChild?: boolean
  tooltip?: boolean
  spinner?: React.ReactNode
}

const RowActionButton = React.forwardRef<HTMLButtonElement, RowActionButtonProps>(
  (
    {
      label,
      action,
      icon,
      tone,
      asChild = false,
      tooltip = true,
      spinner,
      className,
      children,
      type = 'button',
      ...props
    },
    ref,
  ) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <RowActionTooltip label={label} tooltip={tooltip}>
        <Comp
          ref={ref}
          type={asChild ? undefined : type}
          aria-label={label}
          className={cn(rowActionButtonVariants({ tone: toneForAction(action, tone) }), className)}
          {...props}
        >
          {spinner ?? renderActionIcon(action, icon)}
          {children ?? label}
        </Comp>
      </RowActionTooltip>
    )
  },
)
RowActionButton.displayName = 'RowActionButton'

interface RowActionGroupProps extends React.HTMLAttributes<HTMLDivElement> {}

function RowActionGroup({ className, ...props }: RowActionGroupProps) {
  return <div className={cn('flex items-center justify-end gap-1', className)} {...props} />
}

export {
  BorderedRowActionIconButton,
  RowActionButton,
  RowActionGroup,
  RowActionIconButton,
  rowActionButtonVariants,
  rowActionIconButtonVariants,
  rowActionIcons,
  rowActionToneByAction,
  Loader2 as RowActionLoaderIcon,
}
