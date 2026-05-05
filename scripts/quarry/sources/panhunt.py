"""s.panhunt.com pan search source adapter."""
from __future__ import annotations
import os
from .base import HTTPClient, SourceAdapter, _flatten_pan_payload
from ..exceptions import SourceUnavailableError
from ..models import SearchIntent, SearchResult


class PanhuntSource(SourceAdapter):
    name = "panhunt"
    channel = "pan"
    priority = 2

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        token = os.environ.get("PANSOU_TOKEN", "").strip()
            
        url = "https://s.panhunt.com/api/search"
        payload_data = {
            "kw": query,
            "page": page,
        }
        headers = {
            "Referer": "https://s.panhunt.com/"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        payload = http_client.post_json(url, json_data=payload_data, headers=headers)
        if not isinstance(payload, dict):
            return []
        
        if payload.get("code") == "AUTH_TOKEN_MISSING" or payload.get("error"):
            raise SourceUnavailableError(f"panhunt auth error: {payload.get('error', 'unknown')}", source=self.name, url=url)
            
        return _flatten_pan_payload(payload, self.name)
