import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PasswordStrengthMeter } from "../PasswordStrengthMeter";

vi.mock("@/paraglide/messages", () => ({
  signup_password_policy_checking: () => "Checking",
  signup_password_policy_label: () => "Policy",
  signup_password_policy_met: () => "Meets policy",
  signup_password_strength_fair: () => "Could be stronger",
  signup_password_strength_good: () => "Good",
  signup_password_strength_label: () => "Strength",
  signup_password_strength_strong: () => "Strong",
  signup_password_strength_too_short: ({ strength }: { strength: string }) =>
    `${strength}, but too short`,
  signup_password_strength_very_weak: () => "Very weak",
  signup_password_strength_weak: () => "Weak",
  signup_password_too_short: ({ minLength }: { minLength: string }) =>
    `Use at least ${minLength} characters.`,
  signup_password_too_weak: () => "Too weak.",
}));

describe("PasswordStrengthMeter", () => {
  it("shows strength feedback while making clear that a strong password is still too short", () => {
    const { container } = render(
      <PasswordStrengthMeter
        score={4}
        issues={["too_short"]}
        show
        isAcceptable={false}
        estimated={false}
        policy={{
          min_length: 15,
          min_score: 3,
        }}
      />,
    );

    expect(screen.getByText("Strong, but too short")).toBeTruthy();
    expect(screen.queryByText("Very weak")).toBeNull();
    expect(screen.getByText("Use at least 15 characters.")).toBeTruthy();
    expect(container.querySelectorAll('[class*="bg-[var(--color-warning)]"]')).toHaveLength(4);
    expect(container.querySelectorAll('[class*="bg-[var(--color-success)]"]')).toHaveLength(0);
  });

  it("does not show policy compliance while strength is still estimated", () => {
    render(
      <PasswordStrengthMeter
        score={2}
        issues={[]}
        show
        isAcceptable={false}
        estimated
        policy={{
          min_length: 15,
          min_score: 3,
        }}
      />,
    );

    expect(screen.getByText("Checking")).toBeTruthy();
    expect(screen.queryByText("Meets policy")).toBeNull();
  });
});
