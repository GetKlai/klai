import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ButtonHTMLAttributes, JSX, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

let searchParams = { userID: "uid-1", code: "expired-code" };

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (cfg: Record<string, unknown>) => ({
    ...cfg,
    useSearch: () => searchParams,
  }),
}));

vi.mock("@/components/layout/AuthPageLayout", () => ({
  AuthPageLayout: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    asChild: _asChild,
    size: _size,
    ...props
  }: ButtonHTMLAttributes<HTMLButtonElement> & {
    asChild?: boolean;
    size?: string;
  }) => <button {...props}>{children}</button>,
}));

vi.mock("@/lib/auth", () => ({
  readCsrfCookie: () => "csrf-token",
}));

vi.mock("@/lib/password-policy", () => ({
  loadSignupPasswordPolicy: () =>
    Promise.resolve({
      min_length: 14,
      min_score: 3,
    }),
}));

vi.mock("@/paraglide/messages", () => ({
  error_connection: () => "Connection error",
  set_back: () => "Back to login",
  set_done_body: () => "You can now log in.",
  set_done_continue: () => "Log in",
  set_done_heading: () => "Password set",
  set_error_invalid_link: () =>
    "This link has expired or is invalid. Request a new link or ask your admin to resend the invitation.",
  set_error_mismatch: () => "Passwords do not match",
  set_error_server: () => "Failed to set password",
  set_field_confirm: () => "Confirm password",
  set_field_password: () => "Password",
  set_heading: () => "Set password",
  set_hero_body: () => "Choose a password.",
  set_hero_heading: () => "Set your password",
  set_invalid_link: () => "This link is invalid or has expired.",
  set_invalid_link_back: () => "Back to login",
  set_invalid_link_body: () =>
    "Request a new reset link, or ask your admin to resend the invitation if you were invited by email.",
  set_invalid_link_heading: () => "This link has expired",
  set_invalid_link_request_new: () => "Request a new reset link",
  set_submit: () => "Save",
  set_submit_loading: () => "Saving...",
  set_subheading: ({ minLength }: { minLength: string }) =>
    `Use at least ${minLength} characters.`,
  set_subheading_loading: () => "Password policy is loading.",
  signup_password_policy_label: () => "Policy",
  signup_password_policy_checking: () => "Checking",
  signup_password_policy_met: () => "Meets policy",
  signup_password_ready: () => "Ready",
  signup_password_strength_fair: () => "Could be stronger",
  signup_password_strength_good: () => "Good",
  signup_password_strength_label: () => "Strength",
  signup_password_strength_strong: () => "Strong",
  signup_password_strength_very_weak: () => "Very weak",
  signup_password_strength_weak: () => "Weak",
  signup_password_too_short: ({ minLength }: { minLength: string }) =>
    `Use at least ${minLength} characters.`,
  signup_password_too_weak: () => "Choose a less predictable password.",
}));

import { Route } from "../set";

function renderPasswordSetPage() {
  const Cfg = Route as unknown as { component: () => JSX.Element };
  render(<Cfg.component />);
}

describe("PasswordSetPage", () => {
  beforeEach(() => {
    searchParams = { userID: "uid-1", code: "expired-code" };
    vi.restoreAllMocks();
  });

  it("clears any existing BFF session when the reset link is expired", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: "Link has expired or is invalid, request a new reset link",
          }),
          {
            status: 400,
            headers: { "Content-Type": "application/json" },
          },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    renderPasswordSetPage();

    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "Correct horse battery staple 2026!" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "Correct horse battery staple 2026!" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText(/Request a new link or ask your admin/),
    ).toBeTruthy();
    expect(screen.getByText("This link has expired")).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "Request a new reset link" })
        .getAttribute("href"),
    ).toBe("/password/forgot");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/auth/bff/logout",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Token": "csrf-token" },
      }),
    );
  });

  it("does not clear the session for unrelated validation failures", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({ detail: "Password does not meet policy" }),
        {
          status: 400,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    renderPasswordSetPage();

    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "Correct horse battery staple 2026!" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "Correct horse battery staple 2026!" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("Password does not meet policy"),
    ).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("blocks submit with the shared password policy before calling the backend", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    renderPasswordSetPage();

    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "short phrase" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "short phrase" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        screen.getAllByText("Use at least 14 characters.").length,
      ).toBeGreaterThan(0),
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders the password-set subheading from the loaded password policy", async () => {
    renderPasswordSetPage();

    expect(screen.getByText("Password policy is loading.")).toBeTruthy();
    expect(await screen.findByText("Use at least 14 characters.")).toBeTruthy();
  });
});
