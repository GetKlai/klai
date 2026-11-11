import { useEffect, useMemo, useState } from 'react'
import { Loader2, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/apiFetch'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse, WidgetConfig } from '../../-types'
import { useUpdateWidget } from '../../-hooks'
import { usePublishWidgetPreview } from '../../-preview'
import {
  MAX_ROUTES,
  addRoute,
  issuesToErrors,
  removeRoute,
  setDefault,
  toDrafts,
  toRoutes,
  validateRoutes,
  type RouteDraft,
  type RouteError,
  type RouteErrors,
  type RouteField,
} from '../../-contact-routes'

interface Props {
  widget: WidgetDetailResponse
}

function errorText(error: RouteError | undefined): string | undefined {
  if (!error) return undefined
  switch (error.code) {
    case 'required': return m.admin_widgets_routes_error_required()
    case 'too_long': return m.admin_widgets_routes_error_too_long({ max: error.max })
    case 'phone': return m.admin_widgets_routes_error_phone()
    case 'email': return m.admin_widgets_routes_error_email()
    case 'url': return m.admin_widgets_routes_error_url()
    case 'link_pair': return m.admin_widgets_routes_error_link_pair()
    case 'unreachable': return m.admin_widgets_routes_error_unreachable()
    case 'server': return error.message
  }
}

// The routes live on their own tab with one Save for the whole list: a route
// is a few fields of the widget's config, not an entity with a page of its
// own, and the default route has to be chosen across the list.
export function ContactRoutesTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const config = widget.widget_config
  const saved = config.contact_routes
  const [drafts, setDrafts] = useState<RouteDraft[]>(() => toDrafts(saved))
  const [showErrors, setShowErrors] = useState(false)
  const [serverErrors, setServerErrors] = useState<{ byRoute: Record<string, RouteErrors>; general: string[] }>({ byRoute: {}, general: [] })
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  useEffect(() => {
    setDrafts(toDrafts(saved))
    setShowErrors(false)
    setServerErrors({ byRoute: {}, general: [] })
  }, [saved])

  const savedJson = useMemo(() => JSON.stringify(toRoutes(toDrafts(saved))), [saved])
  const isDirty = JSON.stringify(toRoutes(drafts)) !== savedJson
  const { errors: clientErrors, ok } = validateRoutes(drafts)

  // The routes reach the model only after a save, so the preview says so.
  usePublishWidgetPreview('contact', { modelBehaviorDirty: isDirty })

  function change(next: RouteDraft[]) {
    setDrafts(next)
    setServerErrors({ byRoute: {}, general: [] })
  }

  function update(key: string, field: RouteField, value: string) {
    change(drafts.map((d) => (d.key === key ? { ...d, [field]: value } : d)))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!ok) {
      setShowErrors(true)
      return
    }
    const next: WidgetConfig = { ...config, contact_routes: toRoutes(drafts) }
    updateMutation.mutate(
      { widget_config: next },
      {
        onSuccess: () => toast.success(m.admin_shared_success_updated()),
        onError: (error) => {
          if (error instanceof ApiError && error.validationIssues) {
            setServerErrors(issuesToErrors(drafts, error.validationIssues))
          } else {
            setServerErrors({ byRoute: {}, general: [error instanceof Error ? error.message : m.admin_shared_error_generic()] })
          }
        },
      },
    )
  }

  return (
    <section className="space-y-6">
      <div className="space-y-1">
        <h3 className="text-base font-semibold text-gray-900">{m.admin_widgets_routes_title()}</h3>
        <p className="text-sm text-gray-600">{m.admin_widgets_routes_intro({ max: MAX_ROUTES })}</p>
      </div>

      <form onSubmit={handleSubmit} noValidate className="space-y-4">
        {drafts.length === 0 && (
          <p className="rounded-lg border border-dashed border-gray-200 p-5 text-sm text-gray-600">
            {m.admin_widgets_routes_empty()}
          </p>
        )}

        {drafts.map((d, index) => {
          const errors: RouteErrors = { ...(showErrors ? clientErrors[d.key] : {}), ...serverErrors.byRoute[d.key] }
          const id = `widget-route-${d.key}`
          const text = (field: RouteField, label: string, props: { hint?: string; placeholder?: string; type?: string; textarea?: number }) => (
            <Field id={`${id}-${field}`} label={label} hint={props.hint} error={errorText(errors[field])}>
              {props.textarea ? (
                <Textarea
                  value={d[field]}
                  onChange={(e) => update(d.key, field, e.target.value)}
                  rows={props.textarea}
                  placeholder={props.placeholder}
                />
              ) : (
                <Input
                  type={props.type}
                  value={d[field]}
                  onChange={(e) => update(d.key, field, e.target.value)}
                  placeholder={props.placeholder}
                />
              )}
            </Field>
          )
          return (
            <article key={d.key} className="space-y-4 rounded-lg border border-gray-200 bg-white p-5">
              <header className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <h4 className="text-sm font-semibold text-gray-900">
                    {d.name.trim() || m.admin_widgets_routes_untitled({ number: index + 1 })}
                  </h4>
                  {d.is_default && <Badge variant="secondary">{m.admin_widgets_routes_default_badge()}</Badge>}
                </div>
                <div className="flex items-center gap-2">
                  {!d.is_default && (
                    <Button type="button" variant="outline" size="sm" onClick={() => change(setDefault(drafts, d.key))}>
                      {m.admin_widgets_routes_make_default()}
                    </Button>
                  )}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={m.admin_widgets_routes_delete()}
                    onClick={() => setPendingDelete(d.key)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </header>

              {text('name', m.admin_widgets_routes_name_label(), { hint: m.admin_widgets_routes_name_help(), placeholder: m.admin_widgets_routes_name_placeholder() })}
              {text('description', m.admin_widgets_routes_description_label(), { hint: m.admin_widgets_routes_description_help(), placeholder: m.admin_widgets_routes_description_placeholder(), textarea: 3 })}
              {text('do_not_answer', m.admin_widgets_routes_do_not_answer_label(), { hint: m.admin_widgets_routes_do_not_answer_help(), placeholder: m.admin_widgets_routes_do_not_answer_placeholder(), textarea: 2 })}

              {text('referral_text', m.admin_widgets_routes_referral_label(), { hint: m.admin_widgets_routes_referral_help(), placeholder: m.admin_widgets_routes_referral_placeholder(), textarea: 2 })}

              {errors.route && (
                <p role="alert" className="text-sm text-[var(--color-destructive-text)]">{errorText(errors.route)}</p>
              )}
              <p className="text-xs text-gray-600">{m.admin_widgets_routes_channels_help()}</p>
              <div className="grid gap-4 sm:grid-cols-2">
                {text('phone', m.admin_widgets_routes_phone_label(), { type: 'tel', placeholder: m.admin_widgets_routes_phone_placeholder() })}
                {text('email', m.admin_widgets_routes_email_label(), { type: 'email', placeholder: m.admin_widgets_routes_email_placeholder() })}
              </div>
              {text('booking_url', m.admin_widgets_routes_booking_label(), { type: 'url', hint: m.admin_widgets_routes_booking_help(), placeholder: m.admin_widgets_routes_url_placeholder() })}
              <div className="grid gap-4 sm:grid-cols-2">
                {text('link_url', m.admin_widgets_routes_link_url_label(), { type: 'url', hint: m.admin_widgets_routes_link_help(), placeholder: m.admin_widgets_routes_url_placeholder() })}
                {text('link_label', m.admin_widgets_routes_link_label_label(), { placeholder: m.admin_widgets_routes_link_label_placeholder() })}
              </div>
              {text('note', m.admin_widgets_routes_note_label(), { hint: m.admin_widgets_routes_note_help(), placeholder: m.admin_widgets_routes_note_placeholder(), textarea: 2 })}
            </article>
          )
        })}

        <div>
          <Button type="button" variant="outline" size="sm" disabled={drafts.length >= MAX_ROUTES} onClick={() => change(addRoute(drafts))}>
            <Plus className="mr-2 h-4 w-4" />
            {m.admin_widgets_routes_add()}
          </Button>
          {drafts.length >= MAX_ROUTES && (
            <p className="mt-1.5 text-xs text-gray-600">{m.admin_widgets_routes_max_reached({ max: MAX_ROUTES })}</p>
          )}
        </div>

        {serverErrors.general.map((message) => (
          <p key={message} className="text-sm text-[var(--color-destructive)]">{message}</p>
        ))}

        <Button type="submit" disabled={updateMutation.isPending || !isDirty}>
          {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {m.admin_shared_save()}
        </Button>
      </form>

      <AlertDialog open={pendingDelete !== null} onOpenChange={(open) => { if (!open) setPendingDelete(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.admin_widgets_routes_delete_title()}</AlertDialogTitle>
            <AlertDialogDescription>{m.admin_widgets_routes_delete_body()}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_widgets_routes_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => {
                if (pendingDelete) change(removeRoute(drafts, pendingDelete))
                setPendingDelete(null)
              }}
            >
              {m.admin_widgets_routes_delete()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}
