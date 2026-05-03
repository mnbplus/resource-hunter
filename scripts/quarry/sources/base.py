"""Base infrastructure for source adapters: HTTPClient, SourceAdapter, profiles, helpers."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)


def _lenient_ssl_context() -> ssl.SSLContext:
    """Create a lenient SSL context for sites with non-standard TLS (e.g. yts.mx)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Allow legacy TLS renegotiation and older protocols
    ctx.options &= ~ssl.OP_NO_SSLv3  # type: ignore[assignment]
    return ctx


def _urllib_opener() -> urllib.request.OpenerDirector:
    """Build an opener that respects HTTPS_PROXY env var."""
    proxy_url = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("http_proxy")
    )
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPSHandler(context=_lenient_ssl_context()),
    ]
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    return urllib.request.build_opener(*handlers)

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

try:
    from curl_cffi.requests import Session as _CffiSession  # type: ignore[import-untyped]
except ImportError:
    _CffiSession = None  # type: ignore[assignment,misc]

from ..common import (
    clean_share_url,
    compact_spaces,
    extract_password,
    extract_share_id,
    infer_provider_from_url,
    normalize_title,
    parse_quality_tags,
    quality_display_from_tags,
)
from ..models import SearchIntent, SearchResult


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
}

TRACKERS = (
    "&tr=udp://tracker.openbittorrent.com:80"
    "&tr=udp://tracker.opentrackr.org:1337"
    "&tr=udp://open.demonii.com:1337"
    "&tr=udp://tracker.torrent.eu.org:451"
    "&tr=udp://tracker.cyberia.is:6969"
)


@dataclass(frozen=True)
class SourceRuntimeProfile:
    supported_kinds: tuple[str, ...]
    timeout: int
    retries: int
    degraded_score_penalty: int
    cooldown_seconds: int
    failure_threshold: int
    query_budget: int
    default_degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported_kinds": list(self.supported_kinds),
            "timeout": self.timeout,
            "retries": self.retries,
            "degraded_score_penalty": self.degraded_score_penalty,
            "cooldown_seconds": self.cooldown_seconds,
            "failure_threshold": self.failure_threshold,
            "query_budget": self.query_budget,
            "default_degraded": self.default_degraded,
        }


SOURCE_RUNTIME_PROFILES: dict[str, SourceRuntimeProfile] = {
    "upyunso": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=10, retries=0, degraded_score_penalty=6, cooldown_seconds=120, failure_threshold=2, query_budget=2, default_degraded=False,
    ),

    "ps.252035": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=8, retries=1, degraded_score_penalty=10, cooldown_seconds=90, failure_threshold=1, query_budget=1, default_degraded=False,
    ),
    "panhunt": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=8, retries=0, degraded_score_penalty=10, cooldown_seconds=90, failure_threshold=1, query_budget=2, default_degraded=False,
    ),
    "nyaa": SourceRuntimeProfile(
        supported_kinds=("anime", "general"),
        timeout=8, retries=1, degraded_score_penalty=0, cooldown_seconds=180, failure_threshold=2, query_budget=3,
    ),
    "eztv": SourceRuntimeProfile(
        supported_kinds=("tv", "general"),
        timeout=10, retries=1, degraded_score_penalty=0, cooldown_seconds=180, failure_threshold=2, query_budget=3,
    ),
    "tpb": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=10, retries=1, degraded_score_penalty=0, cooldown_seconds=180, failure_threshold=2, query_budget=3,
    ),
    "yts": SourceRuntimeProfile(
        supported_kinds=("movie",),
        timeout=12, retries=1, degraded_score_penalty=16, cooldown_seconds=90, failure_threshold=2, query_budget=2, default_degraded=False,
    ),
    "1337x": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "software", "book", "general"),
        timeout=8, retries=0, degraded_score_penalty=4, cooldown_seconds=180, failure_threshold=2, query_budget=2,
    ),
    "limetorrents": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=10, retries=0, degraded_score_penalty=4, cooldown_seconds=180, failure_threshold=2, query_budget=2,
        default_degraded=True,
    ),
    "fitgirl": SourceRuntimeProfile(
        supported_kinds=("software", "general"),
        timeout=12, retries=0, degraded_score_penalty=2, cooldown_seconds=180, failure_threshold=2, query_budget=2,
    ),
    "torznab": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=15, retries=0, degraded_score_penalty=2, cooldown_seconds=60, failure_threshold=2, query_budget=3,
    ),
    "bitsearch": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=10, retries=0, degraded_score_penalty=2, cooldown_seconds=180, failure_threshold=3, query_budget=2,
    ),
    "torrentmac": SourceRuntimeProfile(
        supported_kinds=("software", "general", "game"),
        timeout=15, retries=0, degraded_score_penalty=2, cooldown_seconds=300, failure_threshold=3, query_budget=1,
    ),
    "annas": SourceRuntimeProfile(
        supported_kinds=("book", "general"),
        timeout=15, retries=0, degraded_score_penalty=4, cooldown_seconds=300, failure_threshold=2, query_budget=1,
        default_degraded=True,
    ),
    "pansou": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=10, retries=0, degraded_score_penalty=6, cooldown_seconds=90, failure_threshold=2, query_budget=2,
    ),
    "dmhy": SourceRuntimeProfile(
        supported_kinds=("anime", "music", "general"),
        timeout=10, retries=1, degraded_score_penalty=0, cooldown_seconds=180, failure_threshold=2, query_budget=3,
    ),
    "bangumi_moe": SourceRuntimeProfile(
        supported_kinds=("anime", "general"),
        timeout=10, retries=1, degraded_score_penalty=0, cooldown_seconds=180, failure_threshold=2, query_budget=2,
    ),
    "torrentgalaxy": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=12, retries=0, degraded_score_penalty=4, cooldown_seconds=180, failure_threshold=2, query_budget=2,
    ),
    "torlock": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=12, retries=0, degraded_score_penalty=4, cooldown_seconds=180, failure_threshold=2, query_budget=2,
    ),
    "ext_to": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=10, retries=0, degraded_score_penalty=4, cooldown_seconds=180, failure_threshold=2, query_budget=2,
        default_degraded=True,
    ),
    "subsplease": SourceRuntimeProfile(
        supported_kinds=("anime",),
        timeout=8, retries=1, degraded_score_penalty=0, cooldown_seconds=180, failure_threshold=2, query_budget=2,
    ),
    "knaben": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=12, retries=0, degraded_score_penalty=6, cooldown_seconds=180, failure_threshold=2, query_budget=2,
        default_degraded=True,
    ),
    "btdig": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "general"),
        timeout=10, retries=0, degraded_score_penalty=8, cooldown_seconds=300, failure_threshold=2, query_budget=1,
        default_degraded=True,
    ),
    "solidtorrents": SourceRuntimeProfile(
        supported_kinds=("movie", "tv", "anime", "music", "software", "book", "general"),
        timeout=10, retries=0, degraded_score_penalty=6, cooldown_seconds=180, failure_threshold=2, query_budget=2,
        default_degraded=True,
    ),
}


def profile_for(source_name: str) -> SourceRuntimeProfile:
    return SOURCE_RUNTIME_PROFILES.get(
        source_name,
        SourceRuntimeProfile(
            supported_kinds=("general",), timeout=10, retries=1,
            degraded_score_penalty=0, cooldown_seconds=180, failure_threshold=2, query_budget=2,
        ),
    )


class HTTPClient:
    """HTTP client with optional httpx / curl_cffi acceleration and urllib fallback.

    Priority chain:  httpx → curl_cffi → urllib
    - httpx:     HTTP/2, connection pooling, fastest for cooperative servers
    - curl_cffi: TLS fingerprint impersonation (JA3/JA4), bypasses network-level bot detection
    - urllib:    zero-dependency fallback, always available
    """

    def __init__(self, retries: int = 1, default_timeout: int = 10) -> None:
        self.retries = retries
        self.default_timeout = default_timeout
        self._session: Any = None
        self._cffi_session: Any = None
        self._use_httpx = _httpx is not None
        self._use_cffi = _CffiSession is not None

    def _ensure_session(self) -> None:
        if self._session is not None or not self._use_httpx:
            return
        try:
            proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("https_proxy") or os.environ.get("http_proxy")
            kwargs: dict[str, Any] = {
                "timeout": self.default_timeout,
                "follow_redirects": True,
                "headers": DEFAULT_HEADERS,
                "transport": _httpx.HTTPTransport(retries=self.retries, verify=_lenient_ssl_context()),
                "verify": _lenient_ssl_context(),
            }
            if proxy_url:
                kwargs["proxy"] = proxy_url
            self._session = _httpx.Client(**kwargs)
        except Exception:
            self._use_httpx = False
            self._session = None

    def _request_httpx(self, url: str, timeout: int | None = None) -> str:
        self._ensure_session()
        if self._session is None:
            return self._request_urllib(url, timeout=timeout)
        effective_timeout = timeout or self.default_timeout
        try:
            response = self._session.get(url, timeout=effective_timeout)
            response.raise_for_status()
            return str(response.text)
        except Exception as exc:
            error_str = str(exc)
            if hasattr(exc, "response") and exc.response is not None:  # type: ignore[union-attr]
                status = exc.response.status_code  # type: ignore[union-attr]
                error_str = f"HTTP {status}"
            raise RuntimeError(error_str) from exc

    def _post_httpx(self, url: str, json_data: dict[str, Any], headers: dict[str, str], timeout: int | None = None) -> str:
        self._ensure_session()
        if self._session is None:
            return self._post_urllib(url, json_data, headers, timeout=timeout)
        effective_timeout = timeout or self.default_timeout
        merged_headers = {**DEFAULT_HEADERS, **headers}
        try:
            response = self._session.post(url, json=json_data, headers=merged_headers, timeout=effective_timeout)
            response.raise_for_status()
            return str(response.text)
        except Exception as exc:
            error_str = str(exc)
            if hasattr(exc, "response") and exc.response is not None:  # type: ignore[union-attr]
                status = exc.response.status_code  # type: ignore[union-attr]
                error_str = f"HTTP {status}"
            raise RuntimeError(error_str) from exc

    def _request_urllib(self, url: str, timeout: int | None = None) -> str:
        timeout = timeout or self.default_timeout
        last_error = ""
        opener = _urllib_opener()
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            try:
                with opener.open(request, timeout=timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return str(response.read().decode(charset, errors="replace"))
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if 400 <= exc.code < 500:
                    break
            except Exception as exc:
                last_error = str(exc)
            if attempt < self.retries:
                time.sleep(0.2 * (attempt + 1))
        raise RuntimeError(last_error or "request failed")

    def _post_urllib(self, url: str, json_data: dict[str, Any], headers: dict[str, str], timeout: int | None = None) -> str:
        timeout = timeout or self.default_timeout
        last_error = ""
        merged_headers = {**DEFAULT_HEADERS, **headers, "Content-Type": "application/json"}
        data = json.dumps(json_data).encode("utf-8")
        opener = _urllib_opener()
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, data=data, headers=merged_headers, method="POST")
            try:
                with opener.open(request, timeout=timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return str(response.read().decode(charset, errors="replace"))
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if 400 <= exc.code < 500:
                    break
            except Exception as exc:
                last_error = str(exc)
            if attempt < self.retries:
                time.sleep(0.2 * (attempt + 1))
        raise RuntimeError(last_error or "request failed")

    

    def _ensure_cffi_session(self) -> None:
        if self._cffi_session is not None or not self._use_cffi:
            return
        try:
            proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("https_proxy") or os.environ.get("http_proxy")
            kwargs: dict[str, Any] = {
                "impersonate": "chrome",
                "timeout": self.default_timeout,
                "headers": DEFAULT_HEADERS,
            }
            if proxy_url:
                kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
            self._cffi_session = _CffiSession(**kwargs)
        except Exception:
            self._use_cffi = False
            self._cffi_session = None

    def _request_cffi(self, url: str, timeout: int | None = None) -> str:
        self._ensure_cffi_session()
        if self._cffi_session is None:
            return self._request_urllib(url, timeout=timeout)
        effective_timeout = timeout or self.default_timeout
        try:
            response = self._cffi_session.get(url, timeout=effective_timeout)
            response.raise_for_status()
            return str(response.text)
        except Exception as exc:
            error_str = str(exc)
            if hasattr(exc, "status_code"):
                error_str = f"HTTP {exc.status_code}"  # type: ignore[union-attr]
            raise RuntimeError(error_str) from exc

    def _post_cffi(self, url: str, json_data: dict[str, Any], headers: dict[str, str], timeout: int | None = None) -> str:
        self._ensure_cffi_session()
        if self._cffi_session is None:
            return self._post_urllib(url, json_data, headers, timeout=timeout)
        effective_timeout = timeout or self.default_timeout
        merged_headers = {**DEFAULT_HEADERS, **headers}
        try:
            response = self._cffi_session.post(url, json=json_data, headers=merged_headers, timeout=effective_timeout)
            response.raise_for_status()
            return str(response.text)
        except Exception as exc:
            error_str = str(exc)
            if hasattr(exc, "status_code"):
                error_str = f"HTTP {exc.status_code}"  # type: ignore[union-attr]
            raise RuntimeError(error_str) from exc

    

    def _request(self, url: str, timeout: int | None = None) -> str:
        if self._use_httpx:
            return self._request_httpx(url, timeout=timeout)
        if self._use_cffi:
            return self._request_cffi(url, timeout=timeout)
        return self._request_urllib(url, timeout=timeout)

    def get_text(self, url: str, timeout: int | None = None) -> str:
        return self._request(url, timeout=timeout)

    def get_json(self, url: str, timeout: int | None = None) -> dict[str, Any] | list[Any]:
        payload = self._request(url, timeout=timeout)
        try:
            parsed: dict[str, Any] | list[Any] = json.loads(payload)
            return parsed
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid json from {url}: {exc}") from exc

    def post_json(self, url: str, json_data: dict[str, Any], headers: dict[str, str] | None = None, timeout: int | None = None) -> dict[str, Any] | list[Any]:
        if self._use_httpx:
            payload = self._post_httpx(url, json_data, headers or {}, timeout=timeout)
        elif self._use_cffi:
            payload = self._post_cffi(url, json_data, headers or {}, timeout=timeout)
        else:
            payload = self._post_urllib(url, json_data, headers or {}, timeout=timeout)
        try:
            parsed: dict[str, Any] | list[Any] = json.loads(payload)
            return parsed
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid json from {url}: {exc}") from exc

    def close(self) -> None:
        if self._session is not None and hasattr(self._session, "close"):
            self._session.close()
            self._session = None
        if self._cffi_session is not None and hasattr(self._cffi_session, "close"):
            self._cffi_session.close()
            self._cffi_session = None

    def __enter__(self) -> "HTTPClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class BrowserClient:
    """Headless browser client powered by Playwright for bypassing Cloudflare and extracting DOM."""
    
    _instance: "BrowserClient | None" = None
    
    def __init__(self, headless: bool = True, timeout: int = 15000):
        self.headless = headless
        self.timeout = timeout
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        
    @classmethod
    def get_instance(cls) -> "BrowserClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def _ensure_browser(self) -> None:
        if self._context is not None:
            return
            
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("playwright is not installed. Run `pip install playwright` and `playwright install chromium`.")
            
        self._playwright = sync_playwright().start()
        
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("https_proxy") or os.environ.get("http_proxy")
        launch_args: dict[str, Any] = {
            "headless": self.headless,
            "args": ["--disable-blink-features=AutomationControlled"]
        }
        if proxy_url:
            launch_args["proxy"] = {"server": proxy_url}
            
        self._browser = self._playwright.chromium.launch(**launch_args)
        
        self._context = self._browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
        )
        # add anti-detection init scripts
        self._context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def get_page(self) -> Any:
        self._ensure_browser()
        return self._context.new_page()

    def get_html(self, url: str, wait_for_selector: str | None = None, timeout: int | None = None) -> str:
        page = self.get_page()
        effective_timeout = timeout or self.timeout
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=effective_timeout)
            if wait_for_selector:
                page.wait_for_selector(wait_for_selector, timeout=effective_timeout)
            # small delay for CF turnstile or dynamic content
            page.wait_for_timeout(2000)
            return str(page.content())
        finally:
            page.close()
            
    def close(self) -> None:
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
            
    @classmethod
    def close_all(cls) -> None:
        if cls._instance:
            cls._instance.close()
            cls._instance = None


# --- Helper functions ---

def _make_magnet(info_hash: str, name: str) -> str:
    return f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(name)}{TRACKERS}"


def _clean_magnet(text: str) -> str:
    return str(html.unescape(text or "")).strip()


def _format_size(size_bytes: int | float | str | None) -> str:
    if size_bytes is None or size_bytes == "" or size_bytes == 0 or size_bytes == "0":
        return ""
    try:
        numeric = float(str(size_bytes))
    except (ValueError, TypeError):
        return str(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if numeric < 1024:
            return f"{numeric:.1f}{unit}"
        numeric /= 1024
    return f"{numeric:.1f}PB"


def _validate_pan_payload(payload: Any, source_name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected payload type from {source_name}")
    if "results" in payload and isinstance(payload["results"], list):
        return payload
    if "data" in payload and isinstance(payload["data"], (list, dict)):
        return payload
    raise RuntimeError(f"unexpected pan payload shape from {source_name}")


def _flatten_pan_payload(payload: dict[str, Any], source_name: str) -> list[SearchResult]:
    payload = _validate_pan_payload(payload, source_name)
    items: list[dict[str, Any]] = []
    if isinstance(payload.get("results"), list):
        items = payload["results"]
    elif isinstance(payload.get("data"), list):
        items = payload["data"]
    elif isinstance(payload.get("data"), dict):
        for provider, values in payload["data"].items():
            for value in values if isinstance(values, list) else []:
                entry = dict(value) if isinstance(value, dict) else {"url": value}
                entry.setdefault("cloud", provider)
                items.append(entry)

    results: list[SearchResult] = []
    for index, item in enumerate(items):
        raw_url = item.get("url") or item.get("link") or item.get("shareUrl") or ""
        cleaned_url = clean_share_url(raw_url)
        if not cleaned_url:
            continue
        provider = item.get("netdiskType") or item.get("cloud") or item.get("type") or infer_provider_from_url(cleaned_url)
        normalized_channel = "torrent" if (provider or "").lower() in {"magnet", "ed2k"} else "pan"
        title = normalize_title(item.get("title") or item.get("name") or "")
        password = item.get("pwd") or item.get("password") or extract_password(raw_url) or extract_password(title)
        quality_tags = parse_quality_tags(title)
        upstream_source = item.get("source") or source_name
        results.append(
            SearchResult(
                channel=normalized_channel, normalized_channel=normalized_channel,
                source=source_name, upstream_source=str(upstream_source),
                provider=(provider or infer_provider_from_url(cleaned_url)).lower(),
                title=title or cleaned_url, link_or_magnet=cleaned_url, password=password,
                share_id_or_info_hash=extract_share_id(cleaned_url, provider_hint=str(provider)),
                size=str(item.get("size") or ""),
                quality=quality_display_from_tags(quality_tags), quality_tags=quality_tags,
                raw={"index": index, **item},
            )
        )
    return results


class SourceAdapter:
    """Base class for all source adapters."""
    name = "base"
    channel = "both"
    priority = 9

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        raise NotImplementedError

    def supports(self, intent: SearchIntent) -> bool:
        profile = profile_for(self.name)
        return intent.kind in profile.supported_kinds or "general" in profile.supported_kinds

    def capability_profile(self) -> dict[str, Any]:
        return profile_for(self.name).to_dict()

    def healthcheck(self, http_client: HTTPClient) -> tuple[bool, str]:
        probe_intent = SearchIntent(
            query="ubuntu", original_query="ubuntu", kind="general",
            channel=self.channel, title_core="ubuntu", title_tokens=["ubuntu"],
        )
        try:
            self.search("ubuntu", probe_intent, limit=1, page=1, http_client=http_client)
            return True, ""
        except Exception as exc:
            return False, str(exc)
