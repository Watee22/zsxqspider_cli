from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from zsxq_pdf.zsxq.cookies import load_cookies


@dataclass
class AuthCheckResult:
    ok: bool
    detail: str


class ZsxqClient:
    """Minimal ZSXQ API client.

    Implements known working endpoints based on community projects and verified patterns:
    - GET /v2/groups/{group_id}/files
    - GET /v2/files/{file_id}/download_url

    The returned download_url is typically a short-lived signed URL to files.zsxq.com (token + expiry).
    """

    def __init__(self, *, base_url: str, cookies_file: Path):
        self.base_url = base_url.rstrip("/")
        self.cookies_file = cookies_file

        cookie_result = load_cookies(cookies_file)
        self.cookies = cookie_result.cookies

        self._client = httpx.Client(
            base_url=self.base_url,
            cookies=self.cookies,
            timeout=httpx.Timeout(10.0, read=30.0),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) zsxq-pdf/0.1",
                "Accept": "application/json, text/plain, */*",
            },
        )

    def _headers_for_group(self, group_id: str) -> dict[str, str]:
        # These headers mirror the web client and help avoid 403 in some environments.
        return {
            "Origin": "https://wx.zsxq.com",
            "Referer": f"https://wx.zsxq.com/dweb2/index/group/{group_id}",
        }

    def list_files(
        self,
        *,
        group_id: str,
        count: int = 20,
        index: str | None = None,
        sort: str = "by_create_time",
    ) -> dict:
        r = self._client.get(
            f"/v2/groups/{group_id}/files",
            params={k: v for k, v in {"count": str(count), "index": index, "sort": sort}.items() if v is not None},
            headers=self._headers_for_group(group_id),
        )
        r.raise_for_status()
        return r.json()

    def list_hashtag_topics(
        self,
        *,
        hid: str,
        count: int = 30,
        end_time: str | None = None,
    ) -> dict:
        params: dict[str, str] = {"count": str(count)}
        if end_time is not None:
            params["end_time"] = end_time
        r = self._client.get(
            f"/v2/hashtags/{hid}/topics",
            params=params,
        )
        r.raise_for_status()
        return r.json()

    def get_file_download_url(self, *, file_id: str, group_id: str) -> str:
        r = self._client.get(
            f"/v2/files/{file_id}/download_url",
            headers=self._headers_for_group(group_id),
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("succeeded"):
            code = data.get("code")
            msg = data.get("message") or data.get("error") or "unknown error"
            raise RuntimeError(f"download_url failed: code={code} msg={msg}")
        url = (data.get("resp_data") or {}).get("download_url")
        if not url:
            raise RuntimeError("download_url missing resp_data.download_url")
        return url

    def auth_check(self, *, group_id: str) -> tuple[bool, str]:
        """Validate cookies by calling a known endpoint.

        We use the group files list endpoint because it is confirmed to exist and
        exercises the same permissions as your actual sync.
        """
        try:
            data = self.list_files(group_id=group_id, count=1)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (401, 403):
                return False, f"HTTP {status}: cookie invalid/expired or no permission"
            return False, f"HTTP {status}: {e}"
        except Exception as e:
            return False, f"request error: {e}"

        if data.get("succeeded"):
            return True, "succeeded=true"
        return False, f"api failed: code={data.get('code')} msg={data.get('message') or data.get('error')}"

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
