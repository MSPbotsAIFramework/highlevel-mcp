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

### Fixes (round 1)

1. **`.dockerignore:10` / `docker build` failure** — removed `README.md` from `.dockerignore` so it stays in the build context that `uv sync --frozen --no-dev` (the project-install sync in the `builder` stage) needs to satisfy hatchling's `readme = "README.md"` validation in `pyproject.toml:5`. Verified by actually building the image: `docker build --platform linux/amd64 --no-cache -t highlevel-mcp:prd21281-fix .` completed (only the three pre-existing, unrelated `UndefinedVar` warnings for `$VERSION`/`$BUILD_DATE`/`$COMMIT_SHA` build args); ran the resulting container, `GET /health` returned `200 {"status":"ok"}`, and `initialize` + `tools/list` returned all 12 tools. Commit `a29e88f`.
2. **No per-call logging** — added `log_call()` in `tools/_common.py`, a decorator stacked under every `@mcp.tool(...)` in `contacts.py` / `opportunities.py` / `calendar.py` / `conversations.py` (12 tools). It logs one INFO line per call: tool name, outcome (`"success"`, or the error envelope's `code` on failure — read from the small structured shape only, not the full body), and latency in ms. `__main__.py:configure_logging()` turns logging on (previously nothing configured it at all, so nothing was ever emitted regardless of level). Commit `a29e88f`.
   - **Self-caught regression while verifying live**: turning on root logging at INFO also surfaced httpx's own per-request log line, which prints the full upstream URL — and every GET call puts the Location ID in that URL's query string (e.g. `GET https://services.leadconnectorhq.com/opportunities/pipelines?locationId=loc-smoke-xyz`). That is exactly the value the tech design says never to log. Fixed by setting `httpx`/`httpcore` loggers to `WARNING` in `configure_logging()`. Verified by rebuilding the image and re-running the same live `tools/call` against a running container with distinctive placeholder credentials (`super-secret-token-999` / `loc-should-not-leak-777`): the resulting container log is exactly `... INFO highlevel_mcp.tools tool=highlevel_list_pipelines outcome=unauthorized latency_ms=152.6` — the httpx request line is gone, and neither credential value appears anywhere in the log.
   - Tests added: `tests/test_tools.py::test_tool_call_logs_outcome_and_latency_on_success`, `tests/test_tools.py::test_tool_call_logs_error_code_without_pii_or_credentials` (asserts the logged message excludes the token, the location ID, the `contact_id` argument, and the raw error message body), `tests/test_observability.py::test_configure_logging_silences_httpx_and_httpcore` (locks in the httpx/httpcore silencing so it can't silently regress).

Re-ran the full suite after both fixes: `uv run pytest -v` → 24 passed, 0 failed (21 pre-existing + 3 new), at commit `a29e88f`.

Not re-verified in this round (same as round 1, no change): the four "needs a real vendor account" test cases (AC3/AC4/AC6/AC7/AC8/AC9/AC10 against live HighLevel data) — no vendor sandbox credentials are available on this machine either; per SOP "no test account is not a blocker".

`gitleaks` is still not installed on this machine (same as round 1) — not run, consistent with "run it if present".
