"""Thin async HTTP client wrapping the HighLevel (GoHighLevel) REST API v2.

Base URL and the mandatory "Version" header are confirmed live against the
real API host, not guessed (2026-11-04, PRD-21281 dev leg):

  curl https://services.leadconnectorhq.com/contacts/
  -> 401 {"statusCode":401,"message":"version header was not found."}
  curl .../contacts/ -H "Version: 2021-07-28"
  -> 401 {"statusCode":401,"message":"No Authorization header found..."}
  curl .../contacts/ -H "Version: 2021-07-28" -H "Authorization: Bearer x"
  -> 401 {"statusCode":401,"message":"Invalid JWT"}  (route exists, auth
     rejected as expected for a placeholder token)

The same probe (route exists vs. 404) confirmed every path this module
calls: GET/POST /contacts/, GET/PUT /contacts/:id, POST /contacts/:id/notes,
POST /contacts/:id/tags, GET /opportunities/search, GET
/opportunities/pipelines, GET /calendars/, GET /calendars/events, GET
/conversations/search, GET /conversations/:id/messages. "2021-07-28" is
accepted by the Version header check (an invalid value would have produced
a different 401 message); it is HighLevel's documented stable v2 version
string.
"""

import asyncio
import base64
import json
from typing import Any

import httpx

from ._json import error_envelope

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 20.0

# One shared connection pool for the process lifetime. No credentials are
# ever stored on it — the token/location are passed per-request via this
# client's own instance attributes, and each instance is built fresh from
# the per-request ContextVar (see server.py), so this is safe to share
# across tenants/requests.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _http_client


# status_code -> (error code, retryable), per SOP §4.2's fixed vocabulary.
# status_code 0 means a network/connection-level failure (no response).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def _classify(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    return "invalid_argument", False


class HighLevelAPIError(Exception):
    def __init__(self, status_code: int, message: str, details: Any = None):
        self.status_code = status_code
        self.message = message
        self.details = details
        super().__init__(f"HighLevel API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = _classify(self.status_code)
        return error_envelope(code, self.message, retryable)


def encode_cursor(payload: dict) -> str:
    """Turn an upstream pagination token (whatever shape HighLevel returns)
    into an opaque cursor string. Tool contracts expose only `cursor`
    (SOP §2.5 / Tech design "uniform pagination contract") — callers never
    see HighLevel's native field names.
    """
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str | None) -> dict:
    if not cursor:
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        # A garbled cursor from a caller is a bad argument, not a crash.
        return {}


class HighLevelClient:
    """Async httpx client wrapping the HighLevel v2 REST API.

    Auth: a Private Integration Token, sent as `Authorization: Bearer
    <token>` (Tech design "Decisions made by DEV01" — chosen over the OAuth
    2.0 marketplace-app flow for v1). Every call is explicitly scoped to the
    Location ID carried on the *current request* (never a module-level
    default) — this is what AC6 (multi-tenant isolation) and TC12 pin down.
    """

    def __init__(self, token: str, location_id: str):
        self._token = token
        self._location_id = location_id

    @property
    def location_id(self) -> str:
        return self._location_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Version": API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _clean_params(self, params: dict | None) -> dict:
        if not params:
            return {}
        return {k: v for k, v in params.items() if v is not None}

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=self._clean_params(params))

    async def post(self, path: str, json_body: Any = None) -> Any:
        return await self._request("POST", path, json_body=json_body)

    async def put(self, path: str, json_body: Any = None) -> Any:
        return await self._request("PUT", path, json_body=json_body)

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: Any = None
    ) -> Any:
        client = _get_http_client()
        url = f"{BASE_URL}{path}"
        headers = self._headers()

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await client.request(
                    method, url, headers=headers, params=params, json=json_body
                )
            except httpx.RequestError as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                    continue
                raise HighLevelAPIError(0, f"{e or type(e).__name__} (url={url})") from e

            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                await asyncio.sleep(self._retry_delay(resp, attempt))
                continue

            return self._handle(resp)

        if last_exc:
            raise HighLevelAPIError(0, f"{last_exc}") from last_exc
        raise HighLevelAPIError(0, "request failed with no response")

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2**attempt, _MAX_BACKOFF_SECONDS)

    def _handle(self, resp: httpx.Response) -> Any:
        if resp.status_code == 204:
            return {"ok": True, "status": 204}
        try:
            body = resp.json()
        except ValueError:
            body = {"raw_response": resp.text}
        if resp.status_code >= 400:
            message = body.get("message") if isinstance(body, dict) else str(body)
            details = body if isinstance(body, dict) else None
            raise HighLevelAPIError(resp.status_code, message or "unknown error", details)
        return body
