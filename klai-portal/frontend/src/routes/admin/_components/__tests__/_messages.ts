/**
 * Shared paraglide message mocks for admin-area tests.
 *
 * Each test file used to inline the same 5 profile_*_label / profile_*_description
 * pairs plus generic admin_users_* / admin_profiles_* keys. Renaming an i18n key
 * meant updating 6+ test files. Centralising here means rename + update ladder
 * helper once, all consumers stay green.
 *
 * Usage:
 *
 *   vi.mock('@/paraglide/messages', () => ({
 *     ...adminMessageMocks,
 *     // override or add page-specific keys here
 *   }))
 */

export const profileLadderMessages = {
  profile_personal_label: () => 'Personal chat',
  profile_personal_description: () => 'Personal description',
  profile_company_label: () => 'Company chat',
  profile_company_description: () => 'Company description',
  profile_kb_manager_label: () => 'Knowledge manager',
  profile_kb_manager_description: () => 'KB manager description',
  profile_group_manager_label: () => 'Group manager',
  profile_group_manager_description: () => 'Group manager description',
  profile_admin_label: () => 'Admin',
  profile_admin_description: () => 'Admin description',
  profile_picker_title: () => 'Profile',
  profile_picker_self_edit_hint: () => 'You cannot change your own profile.',
  profile_picker_save: () => 'Save profile',
  profile_picker_description: () => 'Select profile',
} as const

export const adminUsersMessages = {
  admin_users_field_first_name: () => 'First name',
  admin_users_field_last_name: () => 'Last name',
  admin_users_field_email: () => 'Email',
  admin_users_field_profile: () => 'Profile',
  admin_users_field_language: () => 'Language',
  admin_users_language_nl: () => 'Dutch',
  admin_users_language_en: () => 'English',
  admin_users_col_name: () => 'Name',
  admin_users_col_email: () => 'Email',
  admin_users_col_invited: () => 'Invited',
  admin_users_cancel: () => 'Cancel',
  admin_users_invite_button: () => 'Invite user',
  admin_users_invite_submit: () => 'Send',
  admin_users_invite_submit_loading: () => 'Sending...',
  admin_users_error_invite_generic: () => 'Invitation failed.',
  admin_users_error_edit_generic: () => 'Failed',
  admin_users_edit_heading: () => 'Edit user',
  admin_users_edit_subtitle: () =>
    'Profiles control what tools the user can use. Groups control which knowledge bases the user can access within those tools.',
  admin_users_edit_submit: () => 'Save',
  admin_users_edit_submit_loading: () => 'Saving...',
  admin_users_action_suspend: () => 'Suspend',
  admin_users_action_reactivate: () => 'Reactivate',
  admin_users_action_offboard: () => 'Offboard',
  admin_users_confirm_suspend_title: () => 'Suspend user?',
  admin_users_confirm_suspend_description: () => '...',
  admin_users_confirm_offboard_title: () => 'Offboard user?',
  admin_users_confirm_offboard_description: () => '...',
  admin_settings_saved: () => 'Saved',
} as const

export const adminProfilesMessages = {
  admin_profiles_title: () => 'Profiles',
  admin_profiles_subtitle: () => 'Manage who can do what across the workspace.',
  admin_profiles_back: () => 'Back to profiles',
  admin_profiles_loading: () => 'Loading profiles...',
  admin_profiles_drill_in_empty: () => 'No members in this profile yet.',
  admin_profiles_error_change: () => 'Failed to change profile.',
  admin_profiles_demote_action: () => 'Demote to Personal chat',
  admin_profiles_demote_confirm: ({ name }: { name: string }) =>
    `Demote ${name} to Personal chat?`,
  admin_profiles_demote_success: () => 'User demoted to Personal chat',
  admin_profiles_demote_self_blocked: () => 'You cannot demote yourself.',
  admin_groups_name: () => 'Name',
  admin_groups_members_title: () => 'Members',
  admin_groups_members_add: () => 'Add member',
  admin_groups_members_search_placeholder: () => 'Search…',
  admin_groups_members_success_added: () => 'Added',
} as const

export const adminMessageMocks = {
  ...profileLadderMessages,
  ...adminUsersMessages,
  ...adminProfilesMessages,
} as const
