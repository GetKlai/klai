# .semgrep — klai-specific Semgrep rules

Custom Semgrep rules that augment the registry-based `--config auto` and
`--config p/owasp-top-ten` configs already loaded by
`.github/workflows/semgrep.yml`. These rules express klai-specific
invariants that no public registry rule can encode.

## Layout

```
.semgrep/
├── README.md                              # this file
├── rules/                                 # production rules — loaded in CI
│   └── jwt-peek-without-verify.yml        # SPEC-SEC-AUDIT-2026-04 B4
└── tests/                                 # fixture files for each rule
    ├── jwt_peek_negative.py               # rule MUST NOT fire
    └── jwt_peek_positive.py               # rule MUST fire (1 hit per fn)
```

## Adding a new rule

1. Drop the rule under `rules/<rule-id>.yml`. Use the YAML frontmatter
   header in `jwt-peek-without-verify.yml` as a template (rationale,
   pattern walkthrough, false-negative-bias note).
2. Add fixture files under `tests/<rule_id>_negative.py` and
   `tests/<rule_id>_positive.py`.
3. Verify locally if `semgrep` is installed:
   ```bash
   semgrep --config .semgrep/rules/<rule-id>.yml .semgrep/tests/
   ```
   Expected: zero findings on `*_negative.py`, one or more on `*_positive.py`.
4. Confirm the CI workflow `.github/workflows/semgrep.yml` already loads
   `--config .semgrep/rules/` (rule directories are recursive). No workflow
   change is required to pick up new rule files.

## Why this lives outside ast-grep

`klai-portal/backend/rules/` and other ast-grep `sgconfig.yml`-rooted rule
trees enforce structural patterns that ast-grep handles cleanly (CORS
middleware order, no-secret-equality, no-exec-run). Semgrep is preferred
when the rule needs taint-style or multi-statement reasoning that
ast-grep's lexical patterns cannot express. The B4 rule in this directory
is the canonical example: it must reason about the absence of a downstream
verified-decode call somewhere in the same function body — semgrep's
`pattern-not` with `...` is the natural fit.
