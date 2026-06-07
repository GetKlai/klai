import {
  CreditCard,
  FolderKanban,
  Globe2,
  Key,
  LayoutDashboard,
  MessageSquare,
  Puzzle,
  Settings,
  ShieldCheck,
  Skull,
  Users,
  type LucideIcon,
} from 'lucide-react'
import * as m from '@/paraglide/messages'
import { meetsMinRole, type ProfileRole } from '@/lib/profiles'
import type { MeResponse } from '@/lib/api-me'

export type AdminNavItem = {
  to: string
  label: string
  icon: LucideIcon
  minRole: ProfileRole
  end?: boolean
  requiresFeature?: string
  platformAdminOnly?: boolean
  overviewDescription?: string
}

// SPEC-PORTAL-PROFILES-001 P3.2: per-tab minimum role on the admin sidebar.
// kb_manager and group_manager can enter /admin for groups + instructions.
// Everything else remains admin-only.
//
// SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 4: api-keys, widgets, and mcps are
// additionally platform-unlock-gated via `requiresFeature`. Keep this as the
// single source for sidebar and overview ordering so role/feature visibility
// does not drift between the two navigation surfaces.
export const ADMIN_NAV_ITEMS: AdminNavItem[] = [
  { to: '/admin', label: m.admin_nav_overview(), icon: LayoutDashboard, minRole: 'kb_manager', end: true },
  // SPEC-PLATFORM-ADMIN-001: cross-tenant console, alleen voor Klai-staff
  // (is_platform_admin). Bovenaan zodat het opvalt voor wie het mag zien.
  {
    to: '/admin/platform',
    label: 'Platform',
    icon: Globe2,
    minRole: 'admin',
    platformAdminOnly: true,
    overviewDescription: m.admin_section_platform_description(),
  },
  {
    to: '/admin/users',
    label: m.admin_section_users_title(),
    icon: Users,
    minRole: 'admin',
    overviewDescription: m.admin_section_users_description(),
  },
  // SPEC-PORTAL-ADMIN-UI-001 REQ-11: Profiles between Users and Groups.
  {
    to: '/admin/profiles',
    label: m.admin_section_profiles_title(),
    icon: ShieldCheck,
    minRole: 'admin',
    overviewDescription: m.admin_section_profiles_description(),
  },
  {
    to: '/admin/groups',
    label: m.admin_section_groups_title(),
    icon: FolderKanban,
    minRole: 'group_manager',
    overviewDescription: m.admin_section_groups_description(),
  },
  {
    to: '/admin/widgets',
    label: m.admin_section_widgets_title(),
    icon: MessageSquare,
    minRole: 'admin',
    requiresFeature: 'widgets',
    overviewDescription: m.admin_section_widgets_description(),
  },
  {
    to: '/admin/api-keys',
    label: m.admin_section_api_keys_title(),
    icon: Key,
    minRole: 'admin',
    requiresFeature: 'partner_api',
    overviewDescription: m.admin_section_api_keys_description(),
  },
  // Instructies is samengevoegd met /app/instructions (één pagina, scope-tabs,
  // per-rij edit-rechten via canMutate). De /admin/instructions routes
  // redirecten naar de /app variant. Admins zien Instructies voortaan in
  // het /app sidebar.
  {
    to: '/admin/mcps',
    label: m.admin_section_mcps_title(),
    icon: Puzzle,
    minRole: 'admin',
    requiresFeature: 'custom_mcps',
    overviewDescription: m.admin_section_mcps_description(),
  },
  {
    to: '/admin/billing',
    label: m.admin_section_billing_title(),
    icon: CreditCard,
    minRole: 'admin',
    overviewDescription: m.admin_section_billing_description(),
  },
  {
    to: '/admin/settings',
    label: m.admin_section_settings_title(),
    icon: Settings,
    minRole: 'admin',
    overviewDescription: m.admin_section_settings_description(),
  },
  {
    to: '/admin/danger-zone',
    label: m.admin_section_danger_zone_title(),
    icon: Skull,
    minRole: 'admin',
    overviewDescription: m.admin_section_danger_zone_description(),
  },
]

export function adminNavItemIsVisible(
  item: AdminNavItem,
  effectiveRole: string | undefined,
  me: MeResponse | undefined,
): boolean {
  if (!meetsMinRole(effectiveRole, item.minRole)) return false
  // Platform-admin-only items (cross-tenant console) hidden for everyone except
  // Klai staff in the platform org.
  if (item.platformAdminOnly && !me?.is_platform_admin) return false
  if (item.requiresFeature) {
    // While /api/me is loading, keep the item visible to avoid a flash of
    // missing nav.
    if (!me) return true
    return (me.platform_unlocked_features ?? []).includes(item.requiresFeature)
  }
  return true
}
