import { Checkbox } from '@/components/ui/checkbox'

export function WidgetToggleCard({
  id,
  checked,
  onChange,
  label,
  help,
}: {
  id: string
  checked: boolean
  onChange: (next: boolean) => void
  label: string
  help: string
}) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-gray-200 p-3 klai-hover">
      <Checkbox
        id={id}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div>
        <label htmlFor={id} className="block cursor-pointer text-sm font-medium text-gray-900">
          {label}
        </label>
        <p className="text-xs text-gray-400">{help}</p>
      </div>
    </div>
  )
}
