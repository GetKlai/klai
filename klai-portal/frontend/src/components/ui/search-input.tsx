import * as React from 'react'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'

export interface SearchInputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

/**
 * Text input with a leading search icon. Standardizes the
 * relative-wrapper + absolute icon + `pl-10` pattern that was hand-rolled
 * across list/overview screens (knowledge, users, transcribe, ...).
 */
const SearchInput = React.forwardRef<HTMLInputElement, SearchInputProps>(
  ({ className, type = 'text', ...props }, ref) => {
    return (
      <div className="relative w-full">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
          aria-hidden="true"
        />
        <Input ref={ref} type={type} className={cn('pl-10', className)} {...props} />
      </div>
    )
  },
)
SearchInput.displayName = 'SearchInput'

export { SearchInput }
