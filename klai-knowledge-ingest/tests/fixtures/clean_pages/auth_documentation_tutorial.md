# How sign-in works in YourApp

This guide walks through how authentication works in YourApp and what each option means for end-users. Read this before configuring SSO for your team.

## Overview

YourApp supports multiple sign-in methods:

1. **Email + password** — the default method.
2. **Single sign-on (SSO)** — for teams using Google Workspace, Microsoft Entra, or Okta.
3. **Magic links** — passwordless email-based sign-in for specific user roles.

When a user opens YourApp, they are presented with the sign-in screen. They can choose any of the configured methods. The screen shows the providers your administrator has enabled, plus a primary email field as a fallback.

## How email + password works

The user enters their email address and password. YourApp validates the credentials against the user database and, if valid, issues a session token. The token is stored in an HTTP-only cookie.

Behind the scenes:

- Passwords are hashed with Argon2id before storage.
- Sessions expire after 24 hours by default; configurable per workspace.
- After 5 failed attempts within 15 minutes, the account is temporarily locked and the user receives an email with a recovery link.

## How SSO works

SSO is configured by your administrator in the workspace settings panel.
Once configured, users see a "Continue with X" button on the sign-in screen,
where X is your identity provider. Clicking it redirects to your IdP, where
the user authenticates with their corporate credentials. After successful
authentication, the IdP returns a SAML or OIDC token to YourApp, which
verifies it and issues a session.

If the user is not yet provisioned in YourApp, the just-in-time provisioning
flow creates a user record using the attributes returned by the IdP. Common
attributes mapped: email, full name, group memberships, and department.

## How magic links work

Magic links are short-lived authentication tokens delivered via email. The
user enters their email address on the sign-in screen and receives a link.
Clicking the link logs them into YourApp without requiring a password. The
link expires after 15 minutes and can only be used once.

Magic links are most useful for guest accounts or for environments where
passwords are not appropriate. You can enable them per role in the
authentication settings.

## Frequently asked questions

**Q: Can I disable password sign-in entirely?**
A: Yes. Once you have configured at least one SSO provider, you can require
all users to authenticate via SSO. The email + password fallback is then
removed from the sign-in screen.

**Q: What happens if my IdP is down?**
A: YourApp falls back to email + password if you have not disabled it. If
you have disabled it, your users cannot sign in until your IdP is restored.
Plan for this scenario by leaving at least one administrator account with
password fallback enabled.

**Q: How do I handle ex-employees?**
A: De-provisioning happens automatically via SCIM if your IdP supports it.
Otherwise, you can manually deactivate the user in the workspace admin
panel. Deactivated users cannot sign in.

**Q: Can users have accounts in multiple workspaces?**
A: Yes. A single email can be associated with multiple workspaces, and the
user picks which workspace to enter after sign-in.

## Troubleshooting

If a user reports they cannot sign in:

1. Check the audit log for failed sign-in attempts. The audit log records
   the timestamp, IP, user-agent, and reason for each failure.
2. Verify the user's email is in the workspace user list and their account
   is active.
3. If using SSO, verify the IdP is reachable from YourApp's servers and the
   user's IdP attributes are correctly mapped.
4. If using magic links, check the email logs for delivery failures. Common
   issues are bounced emails and spam-filter rejections.

For deeper debugging, contact YourApp support with the user's email,
approximate sign-in time, and the exact error they encountered. Support can
then look up the corresponding audit-log entries and trace the failure.

## See also

- [Configure SSO step by step](/docs/configure-sso)
- [Audit log reference](/docs/audit-log)
- [Session management](/docs/sessions)
- [SCIM provisioning](/docs/scim)
- [Sign in to YourApp](https://app.yourapp.com/login)
- [Sign in to YourApp](https://app.yourapp.com/login)
- [Sign in to YourApp](https://app.yourapp.com/login)
- [Sign in to YourApp](https://app.yourapp.com/login)
- [Sign in to YourApp](https://app.yourapp.com/login)
- [Sign in to YourApp](https://app.yourapp.com/login)
