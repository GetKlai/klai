/**
 * @purpose Centered page container (`PageContainer`) with owned page padding;
 * authors choose the content width and vertical rhythm
 */
import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type PageContainerWidth = 'lg' | 'xl' | '2xl' | '3xl' | '4xl' | '6xl'
type PageContainerGap = 'none' | '4' | '6' | '8' | '10'

interface PageContainerProps extends HTMLAttributes<HTMLDivElement> {
  width: PageContainerWidth
  gap?: PageContainerGap
}

const widthClasses: Record<PageContainerWidth, string> = {
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '2xl': 'max-w-2xl',
  '3xl': 'max-w-3xl',
  '4xl': 'max-w-4xl',
  '6xl': 'max-w-6xl',
}

const gapClasses: Record<PageContainerGap, string | undefined> = {
  none: undefined,
  '4': 'space-y-4',
  '6': 'space-y-6',
  '8': 'space-y-8',
  '10': 'space-y-10',
}

function PageContainer({
  width,
  gap = 'none',
  className,
  ...props
}: PageContainerProps) {
  return (
    <div
      className={cn(
        'mx-auto px-6 pt-4 pb-10',
        widthClasses[width],
        gapClasses[gap],
        className,
      )}
      {...props}
    />
  )
}

export { PageContainer }
export type { PageContainerGap, PageContainerProps, PageContainerWidth }
