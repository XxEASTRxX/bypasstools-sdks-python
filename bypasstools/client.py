"""BypassTools API client — sync + async implementations."""
from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

DEFAULT_BASE_URL    = "https://api.bypass.tools/api/v1"
DEFAULT_TIMEOUT     = 60
DEFAULT_POLL_INTERVAL = 1.5
DEFAULT_POLL_TIMEOUT  = 90


@dataclass
class BypassResult:
    result_url: str
    cached: bool
    process_time: Optional[float]
    request_id: Optional[str]


@dataclass
class TaskResult:
    status: str                    # "pending" | "processing" | "completed" | "failed"
    result_url: Optional[str] = None
    error: Optional[str] = None


class BypassToolsError(Exception):
    def __init__(self, message: str, code: str = "UNKNOWN_ERROR", status: int = 0):
        super().__init__(message)
        self.code   = code
        self.status = status

    def __repr__(self) -> str:
        return f"BypassToolsError(code={self.code!r}, status={self.status}, message={str(self)!r})"


class BypassTools:
    """
    Synchronous BypassTools API client.

    Usage::

        from bypasstools import BypassTools

        client = BypassTools(api_key="bt_your_key_here")
        result = client.bypass("https://linkvertise.com/example")
        print(result.result_url)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not api_key:
            raise BypassToolsError("api_key is required", "MISSING_API_KEY")
        self._api_key = api_key
        self._base    = base_url.rstrip("/")
        self._timeout = timeout

    # ─── Private helpers ────────────────────────────────────────────────

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url  = self._base + path
        data = json.dumps(body).encode() if body else None
        req  = Request(
            url,
            data=data,
            method=method,
            headers={
                "x-api-key":    self._api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            try:
                payload = json.loads(e.read().decode())
            except Exception:
                payload = {}
            raise BypassToolsError(
                payload.get("message", f"HTTP {e.code}"),
                payload.get("code", "API_ERROR"),
                e.code,
            ) from None
        except URLError as e:
            raise BypassToolsError(str(e.reason), "NETWORK_ERROR") from None
        except TimeoutError:
            raise BypassToolsError("Request timed out", "TIMEOUT") from None

    # ─── Public API ─────────────────────────────────────────────────────

    def bypass(self, url: str, *, refresh: bool = False) -> BypassResult:
        """
        Bypass a URL synchronously and return the result immediately.

        :param url:     The URL to bypass (e.g. a Linkvertise or Loot.link URL)
        :param refresh: Set True to skip cache and force a fresh bypass
        :raises BypassToolsError: on API or network failure
        """
        if not url:
            raise BypassToolsError("url is required", "MISSING_URL")
        data = self._request("POST", "/bypass/direct", {"url": url, "refresh": refresh})
        return BypassResult(
            result_url   = data.get("resultUrl") or data.get("result", ""),
            cached       = data.get("cached", False),
            process_time = data.get("processTime"),
            request_id   = data.get("requestId"),
        )

    def create_task(self, url: str) -> str:
        """
        Create an async bypass task and return the taskId.

        :param url: The URL to bypass
        :returns:   taskId string
        """
        if not url:
            raise BypassToolsError("url is required", "MISSING_URL")
        data = self._request("POST", "/bypass/createTask", {"url": url})
        return data["taskId"]

    def get_task_result(self, task_id: str) -> TaskResult:
        """
        Retrieve the current state of a bypass task.

        :param task_id: The taskId returned by :meth:`create_task`
        """
        if not task_id:
            raise BypassToolsError("task_id is required", "MISSING_TASK_ID")
        data = self._request("GET", f"/bypass/getTaskResult/{task_id}")
        return TaskResult(
            status     = data.get("status", ""),
            result_url = data.get("result"),
            error      = data.get("error"),
        )

    def bypass_async(
        self,
        url: str,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> BypassResult:
        """
        Create a task and block until it completes (or times out).

        :param url:           The URL to bypass
        :param poll_interval: Seconds between status checks (default 1.5)
        :param timeout:       Max seconds to wait before raising (default 90)
        :raises BypassToolsError: if the task fails or times out
        """
        task_id  = self.create_task(url)
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            result = self.get_task_result(task_id)
            if result.status in ("completed", "success"):
                return BypassResult(
                    result_url   = result.result_url or "",
                    cached       = False,
                    process_time = None,
                    request_id   = task_id,
                )
            if result.status == "failed":
                raise BypassToolsError(result.error or "Task failed", "TASK_FAILED")

        raise BypassToolsError("Task timed out waiting for result", "TASK_TIMEOUT")


# ─── Async client (requires Python 3.8+ with aiohttp or httpx) ──────────────
# Optional async variant — only available when aiohttp is installed.
try:
    import asyncio

    class AsyncBypassTools(BypassTools):
        """
        Async variant of :class:`BypassTools`.

        Requires Python ≥ 3.8 and the ``aiohttp`` package::

            pip install bypasstools aiohttp

        Usage::

            import asyncio
            from bypasstools import AsyncBypassTools

            async def main():
                client = AsyncBypassTools(api_key="bt_your_key_here")
                result = await client.bypass("https://linkvertise.com/example")
                print(result.result_url)

            asyncio.run(main())
        """

        async def _arequest(self, method: str, path: str, body: Optional[dict] = None) -> dict:
            try:
                import aiohttp  # noqa: PLC0415
            except ImportError:
                raise BypassToolsError(
                    "aiohttp is required for async usage: pip install aiohttp",
                    "MISSING_DEPENDENCY",
                ) from None

            url = self._base + path
            async with aiohttp.ClientSession() as session:
                kwargs = {
                    "method":  method,
                    "url":     url,
                    "headers": {"x-api-key": self._api_key, "Content-Type": "application/json"},
                    "timeout": aiohttp.ClientTimeout(total=self._timeout),
                }
                if body:
                    kwargs["json"] = body
                try:
                    async with session.request(**kwargs) as resp:
                        data = await resp.json()
                        if resp.status >= 400:
                            raise BypassToolsError(
                                data.get("message", f"HTTP {resp.status}"),
                                data.get("code", "API_ERROR"),
                                resp.status,
                            )
                        return data
                except aiohttp.ClientError as e:
                    raise BypassToolsError(str(e), "NETWORK_ERROR") from None

        async def bypass(self, url: str, *, refresh: bool = False) -> BypassResult:  # type: ignore[override]
            if not url:
                raise BypassToolsError("url is required", "MISSING_URL")
            data = await self._arequest("POST", "/bypass/direct", {"url": url, "refresh": refresh})
            return BypassResult(
                result_url   = data.get("resultUrl") or data.get("result", ""),
                cached       = data.get("cached", False),
                process_time = data.get("processTime"),
                request_id   = data.get("requestId"),
            )

        async def create_task(self, url: str) -> str:  # type: ignore[override]
            if not url:
                raise BypassToolsError("url is required", "MISSING_URL")
            data = await self._arequest("POST", "/bypass/createTask", {"url": url})
            return data["taskId"]

        async def get_task_result(self, task_id: str) -> TaskResult:  # type: ignore[override]
            data = await self._arequest("GET", f"/bypass/getTaskResult/{task_id}")
            return TaskResult(status=data.get("status", ""), result_url=data.get("result"), error=data.get("error"))

        async def bypass_async(  # type: ignore[override]
            self,
            url: str,
            *,
            poll_interval: float = DEFAULT_POLL_INTERVAL,
            timeout: float = DEFAULT_POLL_TIMEOUT,
        ) -> BypassResult:
            task_id  = await self.create_task(url)
            deadline = asyncio.get_event_loop().time() + timeout

            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(poll_interval)
                result = await self.get_task_result(task_id)
                if result.status in ("completed", "success"):
                    return BypassResult(result_url=result.result_url or "", cached=False, process_time=None, request_id=task_id)
                if result.status == "failed":
                    raise BypassToolsError(result.error or "Task failed", "TASK_FAILED")

            raise BypassToolsError("Task timed out", "TASK_TIMEOUT")

except Exception:
    pass  # asyncio unavailable — async client not exposed
