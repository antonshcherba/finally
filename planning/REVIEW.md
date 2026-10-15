# Code Review

Reviewed the working tree against `HEAD` (`28f741f`), including modified and untracked source files. Static review only; I did not run tests or builds.

## Findings

### [P1] Frontend library modules are ignored and missing from the changeset

The root `.gitignore` has a broad `lib/` rule. It ignores `frontend/src/lib/api.ts`, `format.ts`, `types.ts`, and `usePriceStream.ts`, which are imported by the new frontend. They exist in the working directory, but Git does not include them in the changeset. A commit or fresh checkout will therefore fail to build due to unresolved `@/lib/*` imports.

**Fix:** add a targeted negation for `frontend/src/lib/` and its contents after the broad ignore rule, then add these modules to the changeset.

### [P2] Persisted chat history is not shown after reload

`POST /api/chat` persists both sides of each exchange, and the backend uses that history as context for later LLM calls. The frontend initializes `messages` to an empty array and has no request to load saved messages, so reloading the page hides the conversation while it continues to influence assistant responses. This misses the planned conversation-history behavior.

**Fix:** add an endpoint for retrieving recent chat messages and load them when the page starts.

### [P3] README status contradicts the delivered implementation

The README Status section says the API, database, LLM chat, frontend, and Docker packaging are still to be built. Those components are present in this changeset, and Quick Start now documents running the Docker app, so the status section gives contradictory guidance.

**Fix:** update the Status section to describe the current implementation and any remaining work.

## Review notes

The changes add FastAPI routes and lifespan wiring, SQLite persistence, shared trade services, LLM chat, a static frontend, Docker packaging, startup scripts, and API/storage/UI/E2E tests. I did not identify additional actionable defects during this static review. Runtime behavior, dependency resolution, and container startup were not verified.
