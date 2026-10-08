# Review

Reviewed commit `7773832` (`Update plan, add review docs, market docs, and reviewer agents/plugin`) against its parent, `2fcaf9c`.

## Findings

### [P1] Stop hooks use shell command substitution

`.claude/settings.json:13` and `independent-reviewer/hooks/hooks.json:8` put the `codex exec` command inside backticks. The hook executes this as shell command substitution: it runs Codex, captures its output, then attempts to execute that output as a command. The hook will fail after the review, and output may be interpreted as shell syntax. Remove the backticks and provide the command directly.

### [P1] README and summary claim completion while the implementation and plan say work remains

`README.md:9` describes the market data component as complete, and `planning/MARKET_DATA_SUMMARY.md:3,62` says all issues are resolved. But the new `planning/PLAN.md:565-570` explicitly calls out unfinished behavior: rejecting unknown simulator tickers, adding `baseline_price` to prices/SSE, and tracking watchlist ∪ positions. The existing implementation lacks at least the first two. Update the completion claims or implement the listed requirements before describing the module as complete.

### [P1] Massive plan still recommends a snapshot endpoint unavailable on the free tier

`planning/PLAN.md:164-171` says the filtered snapshot call stays within the free-tier limit and recommends polling it every 15 seconds for the free tier. The newly added `planning/MASSIVE_API.md:27-34` says Stocks Basic has no snapshot access and requires Starter or above. These instructions contradict each other and would cause agents to build a free-key path that repeatedly fails. Align §6 with the plan table and explicitly describe the fallback/EOD behavior.

### [P2] Massive ticker validation mistakes missing snapshot data for an unknown symbol

`planning/MARKET_INTERFACE.md:197-203` says `validate_ticker` returns true only when a snapshot result has a price and otherwise treats transient errors separately. But `planning/MASSIVE_API.md:58,148,249` notes snapshots may omit a valid ticker when it has no data yet (including before its first trade) and says a missing result can mean either an unknown symbol or no data yet. As written, valid tickers can be rejected as `unknown_ticker` when the market is closed or before data populates. Define validation independently of a current price (or preserve an explicit indeterminate/unavailable result) so absence of a quote does not mean the symbol is invalid.

### [P2] Reviewer agents lack the tools required by their instructions

`.claude/agents/reviewer.md:4-8` and `.claude/agents/codex-reviewer.md:4-10` restrict the agents to `Read, Glob, Grep`, while their instructions require writing `planning/REVIEW.md`; the Codex reviewer also has to run a shell command. These agents cannot perform the required actions with their declared tools. Grant the needed tools or revise the workflow to assign the write/command to an agent that has them.

### [P2] The Stop review hook is registered twice

The same Stop hook appears in `.claude/settings.json:7-17` and `independent-reviewer/hooks/hooks.json:2-14`. Installing the plugin in a project retaining the project settings will run two reviews for one Stop event, both writing the same report. Register the hook through one path or document/remove the duplicate project-level registration.

### [P3] Marketplace owner and plugin author are placeholders

`.claude-plugin/marketplace.json:3-4,14-16` publishes `me` and `me@example.com` as the owner and author. Replace these before sharing the marketplace so users can identify its maintainer.

## Review scope

Reviewed all files changed in the commit, including the plan, market-data reference docs, README, Claude settings/agents/command, and plugin metadata. Cross-checked claims about existing market behavior against `backend/app/market/` and `planning/MARKET_DATA_SUMMARY.md`. No tests or demo commands were run; this review was limited to correctness and consistency of the changes.

## Review of `ec82ff6` — `Add detailed market data backend design`

### [P1] Free-tier polling cannot use the snapshot endpoint

`planning/MARKET_DATA_DESIGN.md:43,65-68,94,1050-1053,1083-1088` recommends polling the full-market snapshot endpoint on the free tier, with a 15-second interval. `planning/MASSIVE_API.md:27,34,40` says Stocks Basic has no snapshot access and snapshot calls require Starter or above. A free key will therefore fail on every poll and never populate prices. Align this design with the API reference: make tier selection explicit and define the Basic/EOD behavior, or require a snapshot-enabled plan.

### [P2] Missing snapshot results are treated as proof a symbol is invalid

`planning/MARKET_DATA_DESIGN.md:1689` says a ticker that never appears in snapshot results is invalid, while `planning/MASSIVE_API.md:61,249` explains that valid tickers can be absent before their first trade or when snapshot data has not populated. The trade example at `planning/MARKET_DATA_DESIGN.md:1349-1355` then returns “No price available” without distinguishing a valid symbol awaiting data from an invalid symbol. Preserve an unknown/indeterminate state or validate symbols through an endpoint whose result is independent of current snapshot availability.

### [P2] Failed Massive trade requests leave newly added tickers tracked

In the trade flow at `planning/MARKET_DATA_DESIGN.md:1349-1355`, a ticker absent from the source is added before checking the cache. If Massive has no quote yet, the request returns HTTP 400 before the final reconciliation at lines 1356-1357. That ticker is not in the watchlist or positions, but remains in the source’s tracked set and gets polled indefinitely. Reconcile tracking on this failure path (or defer the addition until the ticker is known to be needed).

### Review scope

Reviewed the complete 1,747-line design added by `ec82ff6`, including its implementation examples, requirements, rate-limit assumptions, edge cases, and integration flow. Cross-checked Massive tier and missing-snapshot behavior against `planning/MASSIVE_API.md`. No tests or demo commands were run; this was a documentation and design review.
