import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'

interface BillingFieldProps {
  label: string
  name: string
  type?: string
  value: string
  onChange: (value: string) => void
  hint?: string
  required?: boolean
  placeholder?: string
}

export function BillingField({
  label,
  name,
  type = 'text',
  value,
  onChange,
  hint,
  required,
  placeholder,
}: BillingFieldProps) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={name}>
        {label}
        {!required && (
          <span className="ml-1 text-xs text-gray-400 font-normal">{m.admin_billing_field_optional()}</span>
        )}
      </Label>
      <Input
        id={name}
        name={name}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        placeholder={placeholder}
      />
      {hint && <p className="text-xs text-gray-400">{hint}</p>}
    </div>
  )
}
