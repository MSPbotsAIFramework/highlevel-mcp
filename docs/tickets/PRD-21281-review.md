# PRD-21281 · Code review

## Round 1 · 2026-11-04 · reviewed aa0cb08 (base main@1cddc52)

Verdict: changes requested

### Findings

1. [must-fix] `.dockerignore:10` (interacts with `pyproject.toml:5` and `Dockerfile:22-23`) — `docker build --platform linux/amd64` fails. `.dockerignore` excludes `README.md` from the build context, but `pyproject.toml:5` declares `readme = "README.md"`, which hatchling validates on `uv sync --frozen --no-dev` (the second sync in the `builder` stage, after `COPY . .`, which installs the project itself, not just its dependencies). Reproduced twice, including `--no-cache`:
   ```
   OSError: Readme file does not exist: README.md
   The command '/bin/sh -c uv sync --frozen --no-dev' returned a non-zero code: 1
   ```
   This is the exact gap the dev leg's own Handoff flagged as untested ("no docker group in the sandbox") — it is not an environment-permission issue, it is a real build break, reproducible in an environment that does have Docker. SOP §14 checklist's container item ("docker build ... 可构建并正常启动") is unmet. Fix: drop `README.md` from `.dockerignore` (or stop pointing `pyproject.toml`'s `readme` field at a path excluded from the image build context).

2. [must-fix] No logging exists anywhere in `src/highlevel_mcp/` (confirmed via `grep -rni "logg\|print(" src/` — zero hits outside the one startup-banner `print` in `__main__.py:36`), but Tech design §"Non-functional" (`docs/tickets/PRD-21281.md:164`) explicitly requires: "log tool name, outcome (success/error code), and latency per call; never log the token or location ID value, never log full response bodies." This is a stated tech-design requirement with no implementation and no corresponding note in Self-test/Handoff explaining why it was skipped — per `references/REVIEW.md` §3, an undisclosed gap is treated as a BLOCKER rather than a WARN. Fix: add the per-call log line (tool name, outcome code, latency) described in the tech design, or, if intentionally deferred, say so with a reason in the ticket doc (Risks/Non-goals) instead of leaving it silently absent.

### Not flagged (reviewed, no issue found)

- `uv run pytest -v`: reran independently, 21 passed / 0 failed (matches the `[DEV]` stub's claim).
- README "MSPbots Connector" section: byte-for-byte diff against `reference/readme-connector-section.md` — identical, correctly placed before the Header credential table.
- No CJK characters anywhere in the tree (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .` — clean).
- Credential handling: both headers required, read only from the request, stored only in a `contextvars.ContextVar` reset in `finally` (`server.py`); `test_context_isolation_across_concurrent_requests` independently verified no cross-request leakage under real `asyncio.gather` concurrency.
- Error envelope vocabulary matches the fixed set (`not_configured` / `unauthorized` / `not_found` / `invalid_argument` / `rate_limited` / `upstream_error`); empty results return empty lists, not `not_found`.
- No delete tools exist (`test_no_delete_tools_exist`), matching Product design's stated non-goal.
- Dependencies (`fastmcp` via `mcp[cli]`, `httpx`, `pydantic-settings`, `uvicorn`, `starlette`) match Tech design's "Reuse & cleanup" and are the same set used by sibling vendor-mcp repos — no undisclosed new dependency.
- Pagination contract (`limit` default 50 / hard cap 200, opaque base64 cursor) matches Tech design and AC9; `clamp_limit` handles `None` and `<=0` correctly.
- Retry/backoff on 429/5xx respects `Retry-After`, bounded at 3 retries / 20s cap (`client.py`); confirmed by `test_client_retries_on_429_then_raises_rate_limited` exercising the real retry loop (not just a mock).
