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
  admin_users_subtitle: () => 'Manage who has access to this workspace.',
  admin_users_intro_body: () => 'Users get access through a profile.',
  admin_users_intro_lifecycle: () => 'Groups control knowledge access.',
  admin_users_field_first_name: () => 'First name',
  admin_users_field_last_name: () => 'Last name',
  admin_users_field_email: () => 'Email',
  admin_users_field_profile: () => 'Profile',
  admin_users_field_language: () => 'Language',
  // SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0 - Account type derived from Profile.
  // No selector, no viewer tier, no mismatch warning - the display is a read-only badge.
  admin_users_field_account_type: () => 'Account type',
  admin_users_col_account_type: () => 'Account type',
  admin_users_account_chat_label: () => 'Klai Chat',
  admin_users_account_knowledge_label: () => 'Klai Chat + Knowledge',
  admin_users_account_price_per_month: ({ amount }: { amount: number }) => `€${amount}/mo`,
  admin_users_account_derived_hint: () => 'Derived from the chosen Profile.',
  admin_users_language_nl: () => 'Dutch',
  admin_users_language_en: () => 'English',
  admin_users_col_name: () => 'Name',
  admin_users_col_email: () => 'Email',
  admin_users_col_invited: () => 'Invited',
  admin_users_col_actions: () => 'Actions',
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
  admin_users_action_delete: () => 'Delete',
  admin_users_toast_deleted: () => 'Deleted',
  admin_users_confirm_suspend_title: () => 'Suspend user?',
  admin_users_confirm_suspend_description: () => '...',
  admin_users_confirm_offboard_title: () => 'Offboard user?',
  admin_users_confirm_offboard_description: () => '...',
  admin_users_offboard_wizard_title: ({ name }: { name: string }) => `Offboard ${name}`,
  admin_users_offboard_wizard_description: () => 'Offboard description',
  admin_users_delete_wizard_title: ({ name }: { name: string }) => `Delete ${name}`,
  admin_users_delete_wizard_description: () => 'Delete description',
  admin_users_wizard_loading: () => 'Preparing...',
  admin_users_wizard_preview_error: ({ error }: { error: string }) => `Preview failed: ${error}`,
  admin_users_wizard_tokens_title: () => 'Tokens',
  admin_users_wizard_tokens_description: ({
    apiKeys,
    mcpTokens,
  }: {
    apiKeys: number
    mcpTokens: number
  }) => `${apiKeys} keys ${mcpTokens} tokens`,
  admin_users_wizard_team_kbs: ({ count }: { count: number }) => `Team KBs (${count})`,
  admin_users_wizard_personal_kbs: ({ count }: { count: number }) => `Personal KBs (${count})`,
  admin_users_wizard_transfer: () => 'Transfer',
  admin_users_wizard_transfer_to: () => 'to',
  admin_users_wizard_personal_delete_hint: () => 'Personal KBs are deleted',
  admin_users_wizard_will_be_deleted: () => 'Will be deleted',
  admin_users_offboard_wizard_no_kbs: () => 'No KBs for offboard.',
  admin_users_delete_wizard_no_kbs: () => 'No KBs for delete.',
  admin_settings_saved: () => 'Saved',
} as const

export const adminProfilesMessages = {
  admin_profiles_title: () => 'Profiles',
  admin_profiles_subtitle: () => 'Set what someone can do in Klai.',
  admin_profiles_intro_body: () => 'Profiles are an ascending ladder.',
  admin_profiles_intro_access: () => 'Use groups for knowledge-base access.',
  admin_profiles_back: () => 'Back to profiles',
  admin_profiles_loading: () => 'Loading profiles...',
  admin_profiles_drill_in_empty: () => 'No members in this profile yet.',
  admin_profiles_view_members: () => 'View members',
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
