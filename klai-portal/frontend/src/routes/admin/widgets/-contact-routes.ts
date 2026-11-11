// Draft handling and client-side checks for a widget's contact routes. The
// limits mirror the API's; the server stays the authority and its 422 is
// mapped back onto the field it names (see issuesToErrors).
import type { ValidationIssue } from '@/lib/apiFetch'
import type { ContactRoute } from './-types'

export const MAX_ROUTES = 6
export const LIMITS = {
  name: 60,
  description: 300,
  doNotAnswer: 500,
  referralText: 500,
  phone: 32,
  email: 254,
  linkLabel: 40,
  note: 200,
} as const

// A route as the form holds it: every field a string, `id` only for a route
// the server already knows, `key` for React.
export interface RouteDraft {
  key: string
  id?: string
  name: string
  description: string
  do_not_answer: string
  referral_text: string
  phone: string
  email: string
  booking_url: string
  link_url: string
  link_label: string
  note: string
  is_default: boolean
}

export type RouteField = Exclude<keyof RouteDraft, 'key' | 'id' | 'is_default'>
// Error codes map onto messages in the editor; `server` carries the API's own text.
export type RouteError =
  | { code: 'required' | 'phone' | 'email' | 'url' | 'link_pair' }
  | { code: 'too_long'; max: number }
  | { code: 'server'; message: string }
export type RouteErrors = Partial<Record<RouteField, RouteError>>

let keyCounter = 0
const nextKey = () => `route-${++keyCounter}`

export function emptyDraft(isDefault: boolean): RouteDraft {
  return {
    key: nextKey(), name: '', description: '', do_not_answer: '', referral_text: '', phone: '', email: '',
    booking_url: '', link_url: '', link_label: '', note: '', is_default: isDefault,
  }
}

export function toDrafts(routes: ContactRoute[] | undefined): RouteDraft[] {
  return (routes ?? []).map((r) => ({
    key: nextKey(),
    id: r.id,
    name: r.name,
    description: r.description ?? '',
    do_not_answer: r.do_not_answer ?? '',
    referral_text: r.referral_text ?? '',
    phone: r.phone ?? '',
    email: r.email ?? '',
    booking_url: r.booking_url ?? '',
    link_url: r.link_url ?? '',
    link_label: r.link_label ?? '',
    note: r.note ?? '',
    is_default: r.is_default,
  }))
}

// Optional fields that are empty are left out; `id` goes back unchanged for an
// existing route and is omitted for a new one (the server generates it).
export function toRoutes(drafts: RouteDraft[]): ContactRoute[] {
  const opt = (v: string) => v.trim() || undefined
  return drafts.map((d) => ({
    ...(d.id ? { id: d.id } : {}),
    name: d.name.trim(),
    description: d.description.trim(),
    do_not_answer: opt(d.do_not_answer),
    referral_text: opt(d.referral_text),
    phone: opt(d.phone),
    email: opt(d.email),
    booking_url: opt(d.booking_url),
    link_url: opt(d.link_url),
    link_label: opt(d.link_label),
    note: opt(d.note),
    is_default: d.is_default,
  }))
}

// Adding the first route makes it the default.
export function addRoute(drafts: RouteDraft[]): RouteDraft[] {
  if (drafts.length >= MAX_ROUTES) return drafts
  return [...drafts, emptyDraft(drafts.length === 0)]
}

// Removing the default hands the role to the first route that is left, so a
// non-empty list never ends up without one.
export function removeRoute(drafts: RouteDraft[], key: string): RouteDraft[] {
  const next = drafts.filter((d) => d.key !== key)
  if (next.length > 0 && !next.some((d) => d.is_default)) {
    next[0] = { ...next[0], is_default: true }
  }
  return next
}

export function setDefault(drafts: RouteDraft[], key: string): RouteDraft[] {
  return drafts.map((d) => ({ ...d, is_default: d.key === key }))
}

export function isAbsoluteHttpUrl(value: string): boolean {
  try {
    const { protocol } = new URL(value)
    return protocol === 'http:' || protocol === 'https:'
  } catch {
    return false
  }
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const PHONE_CHARS = /^[\d\s+\-()]+$/

export function validateRoute(d: RouteDraft): RouteErrors {
  const errors: RouteErrors = {}
  const name = d.name.trim()
  if (!name) errors.name = { code: 'required' }
  else if (name.length > LIMITS.name) errors.name = { code: 'too_long', max: LIMITS.name }
  if (d.description.trim().length > LIMITS.description) errors.description = { code: 'too_long', max: LIMITS.description }
  if (d.do_not_answer.trim().length > LIMITS.doNotAnswer) errors.do_not_answer = { code: 'too_long', max: LIMITS.doNotAnswer }
  if (d.referral_text.trim().length > LIMITS.referralText) errors.referral_text = { code: 'too_long', max: LIMITS.referralText }
  if (d.note.trim().length > LIMITS.note) errors.note = { code: 'too_long', max: LIMITS.note }

  const phone = d.phone.trim()
  if (phone) {
    if (phone.length > LIMITS.phone) errors.phone = { code: 'too_long', max: LIMITS.phone }
    else if (!PHONE_CHARS.test(phone) || phone.replace(/\D/g, '').length < 6) errors.phone = { code: 'phone' }
  }
  const email = d.email.trim()
  if (email) {
    if (email.length > LIMITS.email) errors.email = { code: 'too_long', max: LIMITS.email }
    else if (!EMAIL.test(email)) errors.email = { code: 'email' }
  }
  if (d.booking_url.trim() && !isAbsoluteHttpUrl(d.booking_url.trim())) errors.booking_url = { code: 'url' }

  const linkUrl = d.link_url.trim()
  const linkLabel = d.link_label.trim()
  if (linkUrl && !isAbsoluteHttpUrl(linkUrl)) errors.link_url = { code: 'url' }
  if (linkLabel.length > LIMITS.linkLabel) errors.link_label = { code: 'too_long', max: LIMITS.linkLabel }
  else if (!linkUrl !== !linkLabel) errors[linkUrl ? 'link_label' : 'link_url'] = { code: 'link_pair' }
  return errors
}

// The whole list is saveable when no route has an error and, for a non-empty
// list, exactly one route is the default.
export function validateRoutes(drafts: RouteDraft[]): { errors: Record<string, RouteErrors>; ok: boolean } {
  const errors: Record<string, RouteErrors> = {}
  for (const d of drafts) {
    const e = validateRoute(d)
    if (Object.keys(e).length > 0) errors[d.key] = e
  }
  const defaults = drafts.filter((d) => d.is_default).length
  const ok =
    Object.keys(errors).length === 0 &&
    drafts.length <= MAX_ROUTES &&
    (drafts.length === 0 || defaults === 1)
  return { errors, ok }
}

// The API reports a bad field as loc [..., 'contact_routes', <index>, <field>].
// Issues that name a known field of a known route come back as per-field
// errors; the rest is returned as one general message.
export function issuesToErrors(
  drafts: RouteDraft[],
  issues: ValidationIssue[] | undefined,
): { byRoute: Record<string, RouteErrors>; general: string[] } {
  const byRoute: Record<string, RouteErrors> = {}
  const general: string[] = []
  const fields = new Set<string>(Object.keys(emptyDraft(false)).filter((k) => k !== 'key' && k !== 'is_default'))
  for (const issue of issues ?? []) {
    const at = issue.loc.indexOf('contact_routes')
    const index = at >= 0 ? issue.loc[at + 1] : undefined
    const field = at >= 0 ? issue.loc[at + 2] : undefined
    const draft = typeof index === 'number' ? drafts[index] : undefined
    if (draft && typeof field === 'string' && fields.has(field)) {
      byRoute[draft.key] = { ...byRoute[draft.key], [field]: { code: 'server', message: issue.msg } }
    } else {
      general.push(issue.msg)
    }
  }
  return { byRoute, general }
}
