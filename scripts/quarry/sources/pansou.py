"""PanSou (pansou) self-hosted pan aggregation API adapter.

Supports the open-source PanSou project (https://github.com/fish2018/pansou)
which aggregates 13+ cloud drive providers (Baidu, Aliyun, Quark, 115, PikPak,
Tianyi, UC, Xunlei, 123, etc.) plus magnet/ed2k links.

Configure via environment variables:
  PANSOU_API_URL   — base URL of your PanSou instance (e.g. https://so.252035.xyz)
  PANSOU_API_TOKEN — optional auth token (only if AUTH_ENABLED=true on the server)
"""
from __future__ import annotations
import os
from .base import HTTPClient, SourceAdapter, _flatten_pan_payload
from ..exceptions import SourceNetworkError, SourceUnavailableError
from ..models import SearchIntent, SearchResult


class PanSouSource(SourceAdapter):
    name = "pansou"
    channel = "pan"
    priority = 1

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        api_url = os.environ.get("PANSOU_API_URL", "").strip().rstrip("/")
        if not api_url:
            return []

        token = os.environ.get("PANSOU_API_TOKEN", "").strip()

        url = f"{api_url}/api/search"
        payload_data = {
            "kw": query,
            "page": page,
        }
        headers: dict[str, str] = {
            "Referer": f"{api_url}/",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            payload = http_client.post_json(url, json_data=payload_data, headers=headers)
        except Exception as exc:
            err = str(exc)
            if "401" in err or "403" in err:
                import logging
                logging.getLogger(__name__).warning(
                    "pansou auth failed (HTTP %s) — check PANSOU_API_TOKEN in .env",
                    "401" if "401" in err else "403",
                )
                raise SourceUnavailableError(f"pansou auth failed: {exc}", source=self.name, url=url) from exc
            raise SourceNetworkError(str(exc), source=self.name, url=url) from exc

        if not isinstance(payload, dict):
            return []

        if payload.get("code") in ("AUTH_TOKEN_MISSING", "AUTH_TOKEN_INVALID"):
            import logging
            logging.getLogger(__name__).warning("pansou token invalid — skipping (set PANSOU_API_TOKEN in .env)")
            return []

        return _flatten_pan_payload(payload, self.name)
