"""Pan link viability probing — validate cloud drive share links before delivery.

Supported providers:
  - **Aliyun (阿里云盘)**: anonymous share API → alive / cancelled / not-found
  - **Quark (夸克网盘)**: share token API → alive / expired
  - **Baidu (百度网盘)**: share init page → alive / expired / removed
  - **Lanzou (蓝奏云)**: share page dead-signal detection → alive / removed
  - **Tianyi (天翼云盘)**: share info API → alive / expired / not-found
  - **115 (115网盘)**: share page status detection → alive / expired
  - **PikPak**: share info API → alive / not-found

Each probe is designed to complete in ≤ 3 seconds.  Unsupported providers
return ``alive=None`` (unknown) so they are never falsely penalized.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
}

_PROBE_TIMEOUT = 3  # seconds per link


@dataclass
class ProbeResult:
    """Result of probing a single share link."""
    alive: bool | None  # True = alive, False = dead, None = unknown
    reason: str         # human-readable explanation
    title: str          # share title if available


def _post_json(url: str, body: dict[str, Any], *, timeout: int = _PROBE_TIMEOUT) -> dict[str, Any]:
    """POST JSON and return parsed response."""
    data = json.dumps(body).encode("utf-8")
    headers = {**_DEFAULT_HEADERS, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result: dict[str, Any] = json.loads(resp.read().decode("utf-8", errors="replace"))
        return result


def _get_text(url: str, *, timeout: int = _PROBE_TIMEOUT) -> str:
    """GET a URL and return response body as text."""
    req = urllib.request.Request(url, headers=_DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return str(resp.read().decode("utf-8", errors="replace"))


_ALIYUN_SHARE_RE = re.compile(r"(?:aliyundrive|alipan)\.com/s/([A-Za-z0-9]+)")

def _extract_aliyun_share_id(url: str) -> str:
    m = _ALIYUN_SHARE_RE.search(url)
    return m.group(1) if m else ""


def _probe_aliyun(url: str) -> ProbeResult:
    share_id = _extract_aliyun_share_id(url)
    if not share_id:
        return ProbeResult(alive=None, reason="cannot extract share_id", title="")
    try:
        resp = _post_json(
            "https://api.alipan.com/adrive/v3/share_link/get_share_by_anonymous",
            {"share_id": share_id},
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 403):
            return ProbeResult(alive=False, reason=f"HTTP {exc.code}", title="")
        return ProbeResult(alive=None, reason=f"HTTP {exc.code}", title="")
    except Exception as exc:
        return ProbeResult(alive=None, reason=str(exc)[:100], title="")

    # Check response
    if resp.get("code") in ("ShareLink.Cancelled", "ShareLink.Forbidden", "NotFound.ShareLink"):
        return ProbeResult(alive=False, reason=resp.get("code", "cancelled"), title="")
    if resp.get("share_name"):
        return ProbeResult(alive=True, reason="share active", title=resp.get("share_name", ""))
    if resp.get("display_name") or resp.get("file_count"):
        return ProbeResult(alive=True, reason="share active", title=resp.get("display_name", ""))
    # Ambiguous — don't penalize
    return ProbeResult(alive=None, reason="ambiguous response", title="")


_QUARK_SHARE_RE = re.compile(r"pan\.quark\.cn/s/([A-Za-z0-9]+)")

def _extract_quark_pwd_id(url: str) -> str:
    m = _QUARK_SHARE_RE.search(url)
    return m.group(1) if m else ""


def _probe_quark(url: str) -> ProbeResult:
    pwd_id = _extract_quark_pwd_id(url)
    if not pwd_id:
        return ProbeResult(alive=None, reason="cannot extract pwd_id", title="")
    try:
        resp = _post_json(
            "https://drive-m.quark.cn/1/clouddrive/share/sharepage/token",
            {"pwd_id": pwd_id, "passcode": ""},
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 400):
            return ProbeResult(alive=False, reason=f"HTTP {exc.code}", title="")
        return ProbeResult(alive=None, reason=f"HTTP {exc.code}", title="")
    except Exception as exc:
        return ProbeResult(alive=None, reason=str(exc)[:100], title="")

    status = resp.get("status")
    if status == 200:
        data = resp.get("data", {})
        title = data.get("title", "") if isinstance(data, dict) else ""
        return ProbeResult(alive=True, reason="share active", title=title)
    if resp.get("message") and ("expired" in resp["message"].lower() or "不存在" in resp.get("message", "")):
        return ProbeResult(alive=False, reason=resp.get("message", "expired"), title="")
    if status and status >= 400:
        return ProbeResult(alive=False, reason=f"status {status}", title="")
    return ProbeResult(alive=None, reason="ambiguous response", title="")


_BAIDU_SHARE_RE = re.compile(r"pan\.baidu\.com/s/([A-Za-z0-9_-]+)")
_BAIDU_SHORT_RE = re.compile(r"pan\.baidu\.com/share/init\?surl=([A-Za-z0-9_-]+)")

def _extract_baidu_surl(url: str) -> str:
    m = _BAIDU_SHORT_RE.search(url)
    if m:
        return m.group(1)
    m = _BAIDU_SHARE_RE.search(url)
    if m:
        share_id = m.group(1)
        # Strip leading "1" prefix convention
        return share_id[1:] if share_id.startswith("1") and len(share_id) > 10 else share_id
    return ""


def _probe_baidu(url: str) -> ProbeResult:
    surl = _extract_baidu_surl(url)
    if not surl:
        return ProbeResult(alive=None, reason="cannot extract surl", title="")
    try:
        check_url = f"https://pan.baidu.com/share/init?surl={surl}"
        text = _get_text(check_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ProbeResult(alive=False, reason="HTTP 404", title="")
        return ProbeResult(alive=None, reason=f"HTTP {exc.code}", title="")
    except Exception as exc:
        return ProbeResult(alive=None, reason=str(exc)[:100], title="")

    # Check for known dead patterns
    dead_patterns = ["已失效", "已过期", "已删除", "链接不存在", "分享已取消",
                     "此链接分享内容可能因为涉及侵权", "啊哦，你来晚了"]
    lowered = text[:2000]
    for pattern in dead_patterns:
        if pattern in lowered:
            return ProbeResult(alive=False, reason=f"page contains: {pattern}", title="")

    # Alive indicators
    alive_patterns = ["请输入提取码", "提取文件", "文件名"]
    for pattern in alive_patterns:
        if pattern in lowered:
            return ProbeResult(alive=True, reason="share page active", title="")

    # If page loaded without dead indicators, assume probably alive
    if len(text) > 500:
        return ProbeResult(alive=True, reason="page loaded (no dead signals)", title="")

    return ProbeResult(alive=None, reason="ambiguous page", title="")


# ---------------------------------------------------------------------------
# Lanzou (蓝奏云) — page-level dead-signal detection, zero login
# Domains: lanzou.com, lanzoux.com, lanzouq.com, lanzoui.com, lanzout.com, etc.
# ---------------------------------------------------------------------------

_LANZOU_SHARE_RE = re.compile(r"(lanzou[a-z]*\.com)/([A-Za-z0-9_-]+)")

def _extract_lanzou_url(url: str) -> str:
    m = _LANZOU_SHARE_RE.search(url)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    return ""


def _probe_lanzou(url: str) -> ProbeResult:
    canonical = _extract_lanzou_url(url)
    if not canonical:
        return ProbeResult(alive=None, reason="cannot extract lanzou share url", title="")
    try:
        text = _get_text(canonical)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ProbeResult(alive=False, reason="HTTP 404", title="")
        return ProbeResult(alive=None, reason=f"HTTP {exc.code}", title="")
    except Exception as exc:
        return ProbeResult(alive=None, reason=str(exc)[:100], title="")

    snippet = text[:3000]

    # Dead signals
    dead_patterns = ["文件取消分享", "文件不存在", "文件已删除", "分享文件不存在",
                     "来晚了", "文件已取消", "该分享文件已过期"]
    for pattern in dead_patterns:
        if pattern in snippet:
            return ProbeResult(alive=False, reason=f"page: {pattern}", title="")

    # Alive signals — lanzou pages contain download buttons or file info
    alive_patterns = ["下载地址", "文件大小", "class=\"d\"", "class=\"n_box\"",
                      "fn ", "downs ", "f_name"]
    for pattern in alive_patterns:
        if pattern in snippet:
            return ProbeResult(alive=True, reason="share page active", title="")

    # If page loaded with reasonable content, cautiously mark as alive
    if len(text) > 500:
        return ProbeResult(alive=True, reason="page loaded (no dead signals)", title="")

    return ProbeResult(alive=None, reason="ambiguous page", title="")


# ---------------------------------------------------------------------------
# Tianyi / 天翼云盘 (cloud.189.cn) — share info API, zero login
# ---------------------------------------------------------------------------

_TIANYI_SHARE_RE = re.compile(r"cloud\.189\.cn/(?:web/share\?code=|t/)([A-Za-z0-9]+)")

def _extract_tianyi_code(url: str) -> str:
    m = _TIANYI_SHARE_RE.search(url)
    return m.group(1) if m else ""


def _probe_tianyi(url: str) -> ProbeResult:
    share_code = _extract_tianyi_code(url)
    if not share_code:
        return ProbeResult(alive=None, reason="cannot extract share code", title="")
    try:
        api_url = f"https://cloud.189.cn/api/open/share/getShareInfoByCode.action?shareCode={share_code}"
        text = _get_text(api_url)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 400):
            return ProbeResult(alive=False, reason=f"HTTP {exc.code}", title="")
        return ProbeResult(alive=None, reason=f"HTTP {exc.code}", title="")
    except Exception as exc:
        return ProbeResult(alive=None, reason=str(exc)[:100], title="")

    try:
        resp = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — check page content for dead signals
        if "不存在" in text[:1000] or "已过期" in text[:1000] or "已取消" in text[:1000]:
            return ProbeResult(alive=False, reason="page dead signal", title="")
        if len(text) > 500:
            return ProbeResult(alive=True, reason="page loaded", title="")
        return ProbeResult(alive=None, reason="non-json response", title="")

    # JSON response — check status
    res_code = resp.get("res_code") or resp.get("errorCode") or resp.get("code")
    if res_code == 0 or resp.get("shareId"):
        title = resp.get("fileName", "") or resp.get("shareName", "")
        return ProbeResult(alive=True, reason="share active", title=title)
    res_msg = str(resp.get("res_message", "") or resp.get("errorMsg", "") or resp.get("message", ""))
    if any(kw in res_msg for kw in ("不存在", "已过期", "已取消", "已失效", "cancelled")):
        return ProbeResult(alive=False, reason=res_msg[:80], title="")
    if res_code and int(str(res_code)) < 0:
        return ProbeResult(alive=False, reason=f"error code {res_code}", title="")
    return ProbeResult(alive=None, reason="ambiguous api response", title="")


# ---------------------------------------------------------------------------
# 115 网盘 — page-level status detection, conservative (no API login needed)
# ---------------------------------------------------------------------------

_115_SHARE_RE = re.compile(r"115\.com/s/([A-Za-z0-9]+)")

def _extract_115_share_code(url: str) -> str:
    m = _115_SHARE_RE.search(url)
    return m.group(1) if m else ""


def _probe_115(url: str) -> ProbeResult:
    share_code = _extract_115_share_code(url)
    if not share_code:
        return ProbeResult(alive=None, reason="cannot extract share code", title="")
    try:
        check_url = f"https://115.com/s/{share_code}"
        text = _get_text(check_url)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return ProbeResult(alive=False, reason=f"HTTP {exc.code}", title="")
        return ProbeResult(alive=None, reason=f"HTTP {exc.code}", title="")
    except Exception as exc:
        return ProbeResult(alive=None, reason=str(exc)[:100], title="")

    snippet = text[:3000]

    # Dead signals
    dead_patterns = ["文件已失效", "分享已取消", "文件不存在", "已过期",
                     "来晚了", "该分享已删除", "违规内容"]
    for pattern in dead_patterns:
        if pattern in snippet:
            return ProbeResult(alive=False, reason=f"page: {pattern}", title="")

    # Alive signals
    alive_patterns = ["文件大小", "分享时间", "保存到我的网盘", "receive"]
    for pattern in alive_patterns:
        if pattern in snippet:
            return ProbeResult(alive=True, reason="share page active", title="")

    # 115 has heavy anti-bot; if we can't determine, return unknown
    return ProbeResult(alive=None, reason="ambiguous (115 anti-bot)", title="")


# ---------------------------------------------------------------------------
# PikPak — share info API, zero login
# ---------------------------------------------------------------------------

_PIKPAK_SHARE_RE = re.compile(r"mypikpak\.com/s/([A-Za-z0-9_-]+)")

def _extract_pikpak_share_id(url: str) -> str:
    m = _PIKPAK_SHARE_RE.search(url)
    return m.group(1) if m else ""


def _probe_pikpak(url: str) -> ProbeResult:
    share_id = _extract_pikpak_share_id(url)
    if not share_id:
        return ProbeResult(alive=None, reason="cannot extract share_id", title="")
    try:
        api_url = f"https://api-drive.mypikpak.com/drive/v1/share?share_id={share_id}&pass_code_token="
        text = _get_text(api_url)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 400, 403):
            return ProbeResult(alive=False, reason=f"HTTP {exc.code}", title="")
        return ProbeResult(alive=None, reason=f"HTTP {exc.code}", title="")
    except Exception as exc:
        return ProbeResult(alive=None, reason=str(exc)[:100], title="")

    try:
        resp = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ProbeResult(alive=None, reason="non-json response", title="")

    # Check for errors
    error = resp.get("error", "")
    if error:
        error_lower = str(error).lower()
        if "not_found" in error_lower or "invalid" in error_lower or "expired" in error_lower:
            return ProbeResult(alive=False, reason=str(error)[:80], title="")
        return ProbeResult(alive=None, reason=str(error)[:80], title="")

    # Success — share info present
    if resp.get("share_id") or resp.get("file_info") or resp.get("title"):
        title = resp.get("title", "") or resp.get("name", "")
        return ProbeResult(alive=True, reason="share active", title=title)

    return ProbeResult(alive=None, reason="ambiguous api response", title="")


_PROVIDER_PROBERS = {
    "aliyun": _probe_aliyun,
    "quark": _probe_quark,
    "baidu": _probe_baidu,
    "lanzou": _probe_lanzou,
    "tianyi": _probe_tianyi,
    "115": _probe_115,
    "pikpak": _probe_pikpak,
}


class PanLinkProber:
    """Probe pan share links for viability before delivering to the user."""

    def probe(self, url: str, provider: str) -> ProbeResult:
        """Probe a single share link.  Returns ProbeResult."""
        prober = _PROVIDER_PROBERS.get(provider.lower())
        if not prober:
            return ProbeResult(alive=None, reason=f"unsupported provider: {provider}", title="")
        try:
            return prober(url)
        except Exception as exc:
            return ProbeResult(alive=None, reason=f"probe error: {str(exc)[:80]}", title="")

    def probe_batch(
        self,
        items: list[tuple[str, str]],  # [(url, provider), ...]
        max_workers: int = 4,
    ) -> list[ProbeResult]:
        """Probe multiple links concurrently.

        Returns results in the same order as ``items``.
        """
        if not items:
            return []
        results: list[ProbeResult | None] = [None] * len(items)
        with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
            future_to_index = {
                pool.submit(self.probe, url, provider): idx
                for idx, (url, provider) in enumerate(items)
            }
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    results[idx] = ProbeResult(alive=None, reason=f"future error: {str(exc)[:80]}", title="")
        return [r or ProbeResult(alive=None, reason="no result", title="") for r in results]


__all__ = ["PanLinkProber", "ProbeResult"]
