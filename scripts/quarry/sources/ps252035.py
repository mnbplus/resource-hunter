"""ps.252035.xyz pan search source adapter.

Note: ps.252035 rejects httpx requests (HTTP 401) due to TLS fingerprint
or header ordering checks.  We force urllib to avoid this.
"""
from __future__ import annotations
import os
from .base import HTTPClient, SourceAdapter, _flatten_pan_payload
from ..exceptions import SourceNetworkError, SourceUnavailableError
from ..models import SearchIntent, SearchResult


class Ps252035Source(SourceAdapter):
    name = "ps.252035"
    channel = "pan"
    priority = 2

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        token = os.environ.get("PANSOU_TOKEN", "").strip()
        if not token:
            # Skip gracefully if not configured
            return []
            
        url = "https://ps.252035.xyz/api/search"
        payload_data = {
            "kw": query,
            "page": page,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Referer": "https://ps.252035.xyz/"
        }
        
        # Force urllib — ps.252035 rejects httpx (TLS fingerprint / header issue)
        urllib_client = HTTPClient(retries=1, default_timeout=8)
        urllib_client._use_httpx = False
        urllib_client._use_cffi = False
        try:
            payload = urllib_client.post_json(url, json_data=payload_data, headers=headers)
        except Exception as exc:
            if "401" in str(exc):
                import logging
                logging.getLogger(__name__).warning("ps.252035 auth rejected (HTTP 401) — may be transient; if persistent, update PANSOU_TOKEN in .env")
                raise SourceUnavailableError(f"ps.252035 auth rejected: {exc}", source=self.name, url=url) from exc
            raise SourceNetworkError(str(exc), source=self.name, url=url) from exc
        finally:
            urllib_client.close()
        if not isinstance(payload, dict):
            return []
        
        error_code = payload.get("code", "")
        if isinstance(error_code, str) and error_code.startswith("AUTH_TOKEN"):
            import logging
            logging.getLogger(__name__).warning("ps.252035 token invalid/expired — skipping (update PANSOU_TOKEN in .env)")
            return []
            
        return _flatten_pan_payload(payload, self.name)
