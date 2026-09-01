/**
 * @purpose Multi-value select
 */
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
  const triggerRef = React.useRef<HTMLButtonElement>(null)
  const removeButtonRefs = React.useRef(new Map<string, HTMLButtonElement>())
  const pendingFocusRef = React.useRef<{
    removedValue: string
    nextValue: string | null
  } | null>(null)

  React.useLayoutEffect(() => {
    const pendingFocus = pendingFocusRef.current
    if (!pendingFocus || value.includes(pendingFocus.removedValue)) return

    const nextButton = pendingFocus.nextValue
      ? removeButtonRefs.current.get(pendingFocus.nextValue)
      : undefined
    const focusTarget = nextButton ?? triggerRef.current
    focusTarget?.focus()
    pendingFocusRef.current = null
  }, [value])

  function toggle(optionValue: string) {
    if (value.includes(optionValue)) {
      onChange(value.filter((v) => v !== optionValue))
    } else {
      onChange([...value, optionValue])
    }
  }

  function remove(optionValue: string) {
    const removedIndex = value.indexOf(optionValue)
    const nextValue = value.filter((v) => v !== optionValue)
    pendingFocusRef.current = {
      removedValue: optionValue,
      nextValue: nextValue[removedIndex] ?? nextValue[removedIndex - 1] ?? null,
    }
    onChange(nextValue)
  }

  const selectedLabels = value
    .map((v) => options.find((o) => o.value === v)?.label ?? v)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <div
        className={cn(
          'relative flex min-h-9 w-full flex-wrap items-center gap-1 rounded-md border border-input bg-background px-3 py-1.5 pr-9 text-sm shadow-sm ring-offset-background',
          'hover:border-[var(--color-accent)]/50',
          className
        )}
      >
        <PopoverTrigger asChild>
          <button
            ref={triggerRef}
            type="button"
            aria-expanded={open}
            className="absolute inset-0 z-0 flex items-center justify-between rounded-md px-3 focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
          >
            {selectedLabels.length === 0 ? (
              <span className="text-[var(--color-muted-foreground)]">{placeholder}</span>
            ) : (
              <span className="sr-only">{selectedLabels.join(', ')}</span>
            )}
            <ChevronDown className="ml-2 h-4 w-4 shrink-0 text-[var(--color-muted-foreground)]" />
          </button>
        </PopoverTrigger>
        {selectedLabels.map((label, i) => (
          <span
            key={value[i]}
            className="pointer-events-none relative z-10 inline-flex items-center gap-1 rounded-sm bg-[var(--color-accent)]/10 px-1.5 py-0.5 text-xs font-medium text-[var(--color-accent-text)]"
          >
            {label}
            <button
              ref={(button) => {
                if (button) removeButtonRefs.current.set(value[i], button)
                else removeButtonRefs.current.delete(value[i])
              }}
              type="button"
              aria-label={`Verwijder ${label}`}
              onClick={() => remove(value[i])}
              className="pointer-events-auto cursor-pointer rounded-sm opacity-60 hover:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-accent)]"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
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
                    className="group"
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
                        <span className="text-xs text-[var(--color-muted-foreground)] group-data-[selected=true]:text-white/70">
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
