import * as React from 'react'
import { Check, ChevronDown, X } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'

export interface MultiSelectOption {
  value: string
  label: string
  description?: string
}

interface MultiSelectProps {
  options: MultiSelectOption[]
  value: string[]
  onChange: (value: string[]) => void
  placeholder?: string
  className?: string
}

export function MultiSelect({
  options,
  value,
  onChange,
  placeholder = 'Selecteer...',
  className,
}: MultiSelectProps) {
  const [open, setOpen] = React.useState(false)

  function toggle(optionValue: string) {
    if (value.includes(optionValue)) {
      onChange(value.filter((v) => v !== optionValue))
    } else {
      onChange([...value, optionValue])
    }
  }

  function remove(optionValue: string, e: React.MouseEvent) {
    e.stopPropagation()
    onChange(value.filter((v) => v !== optionValue))
  }

  const selectedLabels = value
    .map((v) => options.find((o) => o.value === v)?.label ?? v)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-expanded={open}
          className={cn(
            'flex min-h-9 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-1.5 text-sm shadow-sm ring-offset-background',
            'hover:border-[var(--color-accent)]/50 focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)]',
            'disabled:cursor-not-allowed disabled:opacity-50',
            className
          )}
        >
          <div className="flex flex-wrap gap-1">
            {selectedLabels.length === 0 && (
              <span className="text-[var(--color-muted-foreground)]">{placeholder}</span>
            )}
            {selectedLabels.map((label, i) => (
              <span
                key={value[i]}
                className="inline-flex items-center gap-1 rounded-sm bg-[var(--color-accent)]/10 px-1.5 py-0.5 text-xs font-medium text-[var(--color-accent)]"
              >
                {label}
                <span
                  role="button"
                  aria-label={`Verwijder ${label}`}
                  tabIndex={0}
                  onClick={(e) => remove(value[i], e)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') remove(value[i], e as unknown as React.MouseEvent) }}
                  className="cursor-pointer opacity-60 hover:opacity-100"
                >
                  <X className="h-3 w-3" />
                </span>
              </span>
            ))}
          </div>
          <ChevronDown className="ml-2 h-4 w-4 shrink-0 text-[var(--color-muted-foreground)]" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
        <Command>
          <CommandInput placeholder="Zoeken..." />
          <CommandList>
            <CommandEmpty>Geen opties gevonden.</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const selected = value.includes(option.value)
                return (
                  <CommandItem
                    key={option.value}
                    value={option.value}
                    onSelect={() => toggle(option.value)}
                  >
                    <div className={cn(
                      'mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-[var(--color-border)]',
                      selected && 'border-[var(--color-accent)] bg-[var(--color-accent)]'
                    )}>
                      {selected && <Check className="h-3 w-3 text-white" />}
                    </div>
                    <div className="flex flex-col">
                      <span>{option.label}</span>
                      {option.description && (
                        <span className="text-xs text-[var(--color-muted-foreground)]">
                          {option.description}
                        </span>
                      )}
                    </div>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
