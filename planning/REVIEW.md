# Code Review

Reviewed all changes since `HEAD`, including the tracked settings and review-file changes and the newly added `.claude` GSD installation files. Static review only; I did not run tests.

## Findings

### [P1] Use a portable Node executable for registered hooks

`.claude/settings.json:18` (also lines 37, 47, 69, 79, and 89)

The JavaScript hook commands invoke `/opt/homebrew/bin/node`, a machine-specific Homebrew path. On Intel Macs, Linux, CI, or systems with Node installed elsewhere, those hooks cannot start. This disables the update check and the context, injection-scanning, and guard hooks. Invoke `node` through `PATH` or use a runtime discovery mechanism.

### [P2] Include the helper required by the commit-validation hook

`.claude/hooks/gsd-validate-commit.sh:25`

When `hooks.community` is enabled, this script requires `hooks/lib/git-cmd.js` to identify Git commit commands, but no such file is included in the added `.claude/hooks` tree or the installation manifest. The failed `require()` is redirected and treated as “not a commit,” so the hook exits successfully without validating any commit message. Include the helper in the install, or implement the classification in this script.

## Scope

These are the actionable issues found in the changes reviewed.
