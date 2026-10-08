# Review

Reviewed changes since `14550e1` (`Ready for Teams`).

## Findings

### [P1] Stop hook command uses shell command substitution

`.claude/settings.json:13` and `independent-reviewer/hooks/hooks.json:8` wrap `codex exec` in backticks. The hook runner treats this as shell command substitution: it runs Codex, captures its output, then tries to execute that output as a command. The review may run, but the hook will then fail (and arbitrary output could be interpreted as a command). Remove the backticks and configure the command directly.

### [P1] Market data is described as complete while required behavior is missing

`README.md:9` says the market data component is complete and points to `planning/MARKET_DATA_SUMMARY.md:3`, which says all issues are resolved. However, `planning/PLAN.md:565-570` explicitly lists outstanding requirements: rejecting unknown simulator tickers, adding `baseline_price` to `PriceUpdate`/SSE, and maintaining the union of watchlist and open-position tickers. The current implementation confirms at least the first two are absent (`backend/app/market/simulator.py:146-152` accepts arbitrary symbols; `backend/app/market/models.py:9-17,39-48` has no baseline field). Either complete those requirements or make the status/summary accurately describe them as pending; the current claim can lead downstream work to rely on behavior that is not present.

### [P2] Read-only reviewer agents are told to write a report

`.claude/agents/reviewer.md:4` and `.claude/agents/codex-reviewer.md:4` restrict tools to `Read, Glob, Grep`, but their instructions require writing `planning/REVIEW.md` (and the latter requires running a shell command). These agents cannot fulfill their stated task with the tools they are granted. Add the required write/command tools or change the workflow so a capable agent performs the write.

### [P2] The same Stop review is configured in two places

The identical Stop hook is present in `.claude/settings.json:7-17` and `independent-reviewer/hooks/hooks.json:2-14`. When this plugin is installed in a project that retains the settings hook, one Stop event can launch two full reviews, competing to overwrite `planning/REVIEW.md` and doubling runtime/cost. Choose one registration path or make the two hooks serve distinct purposes.

### [P3] Marketplace owner and author are placeholders

`.claude-plugin/marketplace.json:3-4,14-16` publishes `me` / `me@example.com` as the marketplace owner and plugin author. Replace these before sharing the marketplace so users can identify its maintainer.

## Review scope

Reviewed all tracked and untracked changes present since `14550e1`, including the Claude settings, marketplace/plugin metadata, agent and command definitions, README, and plan changes. Inspected the existing market data model and simulator where the changed documentation describes their behavior. No tests or demo commands were run; this review focuses on correctness and consistency of the changes.
