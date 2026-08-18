---
paths:
  - "rules/**/*.{yml,yaml}"
  - "sgconfig.yml"
  - ".gitignore"
---
# ast-grep project rules

A rule file can work with `ast-grep scan --rule <file>` while being absent from
the repository and project scan. Before trusting a new rule:

1. Run `git check-ignore -v --no-index <rule-file>` and rename or explicitly
   unignore it if a broad ignore pattern matches. The current `*-secret.*`
   pattern, for example, also matches a YAML rule ending in `-secret.yml`.
2. Register the rule through `sgconfig.yml` and run the project scan with
   `ast-grep scan -c sgconfig.yml --inspect summary .`.
3. Confirm the inspection output includes the new rule, then add positive and
   negative fixtures for its intended syntax.

Do not treat a one-file `--rule` invocation as proof that CI or the configured
project scan will execute the rule.
