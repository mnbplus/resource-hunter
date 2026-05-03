"""UP云搜 pan search source adapter (reverse-engineered encrypted API).

Resolves real cloud-drive share links (Quark, Aliyun, Baidu, etc.)
via automatic ephemeral account registration.  No manual login or
stored credentials required — each token batch is generated on the fly
and discarded when its quota (``down_count``) is exhausted.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import logging
import random
import re
import string
import threading
import urllib.parse
import urllib.request
import uuid
from .base import HTTPClient, SourceAdapter
from ..common import normalize_title, parse_quality_tags, quality_display_from_tags
from ..models import SearchIntent, SearchResult

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    _HAS_CRYPTO = True
except ImportError:
    try:
        from Cryptodome.Cipher import AES  # type: ignore[no-redef]
        from Cryptodome.Util.Padding import pad, unpad  # type: ignore[no-redef]
        _HAS_CRYPTO = True
    except ImportError:
        _HAS_CRYPTO = False
        AES = None  # type: ignore[assignment,misc]
        pad = None  # type: ignore[assignment]
        unpad = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_AES_KEY = b"qq1920520460qqxx"
_AES_IV = _AES_KEY
_HMAC_SECRET = b"upyunso_hmac_s3cr3t_2026"

_PAN_TYPE_MAP = {
    "kuake": "quark", "ali": "aliyun", "baidu": "baidu",
    "xunlei": "xunlei", "uc": "uc", "lanzou": "lanzou", "189": "tianyi",
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")

_BASE_URL = "https://www.upyunso.com"
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": f"{_BASE_URL}/",
    "Origin": _BASE_URL,
}

# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------

def _aes_encrypt(text: str) -> str:
    cipher = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV)
    ct = cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))
    return base64.b64encode(ct).decode("utf-8")


def _aes_decrypt(b64text: str) -> str:
    cipher = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV)
    pt = unpad(cipher.decrypt(base64.b64decode(b64text)), AES.block_size)
    return pt.decode("utf-8")


def _build_signed_params(params: dict) -> dict:
    """Encrypt + HMAC-sign *params* exactly like the JS interceptor."""
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=16))

    sign_dict: dict[str, str] = {}
    for k, v in params.items():
        if k in ("_sign", "__payload"):
            continue
        sv = json.dumps(v, separators=(",", ":")) if isinstance(v, (dict, list)) else v
        if sv is None or str(sv) == "":
            continue
        sign_dict[k] = str(sv)
    sign_dict["_nonce"] = nonce

    sign_str = "&".join(f"{k}={v}" for k, v in sorted(sign_dict.items()))
    sign = hmac.new(_HMAC_SECRET, sign_str.encode("utf-8"), hashlib.sha256).hexdigest()
    payload = _aes_encrypt(json.dumps(params, separators=(",", ":")))
    return {"__payload": payload, "_nonce": nonce, "_sign": sign}


def _decrypt_response(resp: dict | list) -> dict | list:
    """If *resp* is an encrypted envelope, decrypt and parse it."""
    if isinstance(resp, dict) and resp.get("__encrypted") and resp.get("data"):
        parsed: dict | list = json.loads(_aes_decrypt(resp["data"]))
        return parsed
    return resp


def _post_form(url: str, params: dict) -> dict:
    """POST with application/x-www-form-urlencoded (signed + encrypted)."""
    signed = _build_signed_params(params)
    body = urllib.parse.urlencode(signed).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={**_DEFAULT_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = json.loads(r.read().decode("utf-8"))
    return _decrypt_response(raw)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Token pool — ephemeral account auto-registration
# ---------------------------------------------------------------------------

class _TokenPool:
    """Manages a pool of disposable auth tokens.

    Each ephemeral registration yields ``down_count`` link-resolve credits
    (currently 2 per account).  When credits are exhausted the pool
    transparently registers a new account.

    Thread-safe — multiple search threads can draw tokens concurrently.
    """

    _instance: "_TokenPool | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._token: str = ""
        self._remaining: int = 0

    @classmethod
    def get(cls) -> "_TokenPool":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def acquire_token(self) -> str:
        """Return a token with ≥ 1 remaining credit, or ``""`` on failure."""
        with self._lock:
            if self._token and self._remaining > 0:
                self._remaining -= 1
                return self._token
            # Need a fresh account
            token, credits = self._register()
            if token and credits > 0:
                self._token = token
                self._remaining = credits - 1  # reserve 1 for this call
                return token
            return ""

    def report_exhausted(self) -> None:
        """Mark the current token as rate-limited (429)."""
        with self._lock:
            self._remaining = 0

    def report_invalid(self) -> None:
        """Mark the current token as invalid (401)."""
        with self._lock:
            self._token = ""
            self._remaining = 0

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _register() -> tuple[str, int]:
        """Register an ephemeral account and return ``(token, down_count)``."""
        uid = uuid.uuid4().hex[:12]
        email = f"rh_{uid}@rh.io"
        passwd = f"Rh{uid}!"
        try:
            resp = _post_form(f"{_BASE_URL}/api/register", {"email": email, "passwd": passwd})
        except Exception as exc:
            logger.warning("upyunso register failed: %s", exc)
            return "", 0

        if not isinstance(resp, dict) or resp.get("status") != "success":
            msg = resp.get("msg", "") if isinstance(resp, dict) else str(resp)
            logger.warning("upyunso register rejected: %s", msg)
            return "", 0

        result = resp.get("result", {})
        token = result.get("token", "")
        credits = int(result.get("down_count", 0))
        if token:
            logger.debug("upyunso ephemeral account registered (%d credits)", credits)
        return token, credits


# ---------------------------------------------------------------------------
# Source adapter
# ---------------------------------------------------------------------------

class UpyunsoSource(SourceAdapter):
    name = "upyunso"
    channel = "pan"
    priority = 1

    def search(self, query: str, intent: SearchIntent, limit: int, page: int, http_client: HTTPClient) -> list[SearchResult]:
        params = {
            "keyword": query,
            "pan_type": "all",
            "file_type": "all",
            "time_range": "all",
            "page": page,
        }
        signed = _build_signed_params(params)
        qs = urllib.parse.urlencode(signed)
        url = f"{_BASE_URL}/api/search?{qs}"

        try:
            resp = http_client.get_json(url)
        except Exception as exc:
            raise RuntimeError(f"upyunso request failed: {exc}") from exc

        resp = _decrypt_response(resp)  # type: ignore[arg-type]

        if not isinstance(resp, dict) or resp.get("status") != "success":
            return []

        raw_list = resp.get("result", {}).get("list", [])
        pool = _TokenPool.get()
        resolve_active = True  # stays True until a fatal failure

        results: list[SearchResult] = []
        for item in raw_list[:limit]:
            rid = item.get("rid", "")
            raw_title = _HTML_TAG_RE.sub("", item.get("title", ""))
            title = normalize_title(raw_title)
            if not title or not rid:
                continue

            pan_type_key = item.get("pan_type", item.get("pan_name", ""))
            provider = _PAN_TYPE_MAP.get(pan_type_key, pan_type_key)

            # Attempt to resolve real share link
            real_url = ""
            if resolve_active:
                real_url, resolve_active = self._try_resolve(rid, pool, http_client)

            link = real_url if real_url else f"{_BASE_URL}/resource/{rid}"

            # Extract quality info from title and file_list
            file_list_str = item.get("file_list", "")
            combined_text = f"{title} {file_list_str}"
            quality_tags = parse_quality_tags(combined_text)

            results.append(
                SearchResult(
                    channel="pan", normalized_channel="pan",
                    source=self.name, upstream_source=self.name,
                    provider=provider,
                    title=title, link_or_magnet=link,
                    share_id_or_info_hash=rid,
                    quality=quality_display_from_tags(quality_tags),
                    quality_tags=quality_tags,
                    raw={
                        "title": raw_title,
                        "pan_type": item.get("pan_type_name", ""),
                        "insert_time": item.get("insert_time", ""),
                        "check_time": item.get("check_time", ""),
                        "file_type": item.get("file_type", ""),
                        "resolved": bool(real_url),
                    },
                )
            )
        return results

    # -- link resolution ---------------------------------------------------

    @staticmethod
    def _try_resolve(rid: str, pool: _TokenPool, http_client: HTTPClient) -> tuple[str, bool]:
        """Try to resolve a single rid.

        Returns ``(real_url, should_continue)``.
        On 429 the pool auto-rotates to a fresh account; only gives up
        after two consecutive registration failures.
        """
        for _attempt in range(2):
            token = pool.acquire_token()
            if not token:
                return "", False  # registration failed — stop resolving

            result = _resolve_link(rid, token, http_client)
            if result == "__rate_limited__":
                pool.report_exhausted()
                continue  # retry with fresh token from new account
            if result == "__auth_failed__":
                pool.report_invalid()
                continue  # retry with fresh token
            if result:
                return result, True
            return "", True  # non-fatal failure, keep trying next rid

        return "", True  # exhausted retries but don't block other rids


def _resolve_link(rid: str, token: str, http_client: HTTPClient) -> str:
    """Call ``/api/resource/{rid}/link`` to get the real share URL.

    Returns the real URL string, or one of two sentinel values:
    - ``"__auth_failed__"`` on 401
    - ``"__rate_limited__"`` on 429
    - ``""`` on any other failure
    """
    params = {"token": token}
    signed = _build_signed_params(params)
    qs = urllib.parse.urlencode(signed)
    url = f"{_BASE_URL}/api/resource/{rid}/link?{qs}"

    try:
        resp = http_client.get_json(url)
    except RuntimeError as exc:
        err = str(exc)
        if "401" in err:
            logger.debug("upyunso link 401 for rid=%s — token expired", rid)
            return "__auth_failed__"
        if "429" in err:
            logger.debug("upyunso link 429 for rid=%s — quota exhausted", rid)
            return "__rate_limited__"
        logger.debug("upyunso link failed for rid=%s: %s", rid, exc)
        return ""
    except Exception as exc:
        logger.debug("upyunso link failed for rid=%s: %s", rid, exc)
        return ""

    resp = _decrypt_response(resp)  # type: ignore[arg-type]
    if isinstance(resp, dict) and resp.get("status") == "success":
        real_url = resp.get("result", {}).get("real_url", "")
        if real_url:
            logger.debug("upyunso resolved rid=%s → %s", rid, real_url[:80])
            return str(real_url)
    return ""
