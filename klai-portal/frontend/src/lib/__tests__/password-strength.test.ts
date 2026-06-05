import { describe, expect, it } from "vitest";

import {
  basicSignupPasswordIssues,
  evaluateSignupPassword,
} from "../password-strength";

const policy = {
  min_length: 15,
  min_score: 3,
};

describe("signup password strength", () => {
  it("accepts a strong passphrase without legacy composition requirements", async () => {
    const result = await evaluateSignupPassword(
      "violet meadow lantern orbit",
      ["mark@example.com", "Mark", "Example BV"],
      policy,
    );

    expect(result.isAcceptable).toBe(true);
    expect(result.issues).toEqual([]);
  });

  it("does not require uppercase, lowercase, number, or symbol classes", () => {
    expect(
      basicSignupPasswordIssues("violet meadow lantern orbit", policy),
    ).toEqual([]);
  });

  it("counts Unicode code points for minimum length like the backend", () => {
    expect(
      basicSignupPasswordIssues("Aa1!😀", {
        ...policy,
        min_length: 6,
      }),
    ).toContain("too_short");
  });

  it("keeps strength separate from policy compliance", async () => {
    const result = await evaluateSignupPassword(
      "correct horse",
      ["mark@example.com", "Mark", "Example BV"],
      policy,
    );

    expect(result.isAcceptable).toBe(false);
    expect(result.issues).toContain("too_short");
  });

  it("rejects passwords based on personal context", async () => {
    const result = await evaluateSignupPassword(
      "Mark!Vletter",
      ["mark@voys.nl", "Mark", "Vletter", "Voys"],
      policy,
    );

    expect(result.isAcceptable).toBe(false);
    expect(result.issues).toContain("too_predictable");
  });
});
