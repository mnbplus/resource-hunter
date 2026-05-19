from __future__ import annotations

import importlib.util
import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

from .models import SearchIntent, SearchResult
from .sources.base import HTTPClient, SourceAdapter


class RecordingHTTPClient(HTTPClient):
    """HTTPClient test double that records whether adapters use it."""

    def __init__(self) -> None:
        super().__init__(retries=0, default_timeout=1)
        self.used = False

    def get_text(self, url: str, timeout: int | None = None) -> str:
        self.used = True
        return ""

    def get_json(self, url: str, timeout: int | None = None) -> dict[str, Any] | list[Any]:
        self.used = True
        return {}

    def post_json(
        self,
        url: str,
        json_data: dict[str, Any],
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any] | list[Any]:
        self.used = True
        return {}


def _load_module(path: Path) -> tuple[Any | None, str]:
    if not path.is_file():
        return None, f"file not found: {path}"
    package_root = Path(__file__).resolve().parent
    try:
        relative_path = path.resolve().relative_to(package_root)
    except ValueError:
        relative_path = None
    if relative_path is not None:
        module_name = "quarry." + ".".join(relative_path.with_suffix("").parts)
        try:
            return importlib.import_module(module_name), ""
        except Exception as exc:
            return None, f"module import failed: {exc}"
    module_name = f"_quarry_validate_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        return None, f"cannot load module spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        return None, f"module import failed: {exc}"
    return module, ""


def _adapter_classes(module: Any) -> list[type[SourceAdapter]]:
    classes: list[type[SourceAdapter]] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, SourceAdapter) and obj is not SourceAdapter:
            classes.append(obj)
    return classes


def _validate_adapter(adapter: SourceAdapter, run_search: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(getattr(adapter, "name", None), str) or not adapter.name.strip():
        errors.append("name must be a non-empty string")
    if adapter.channel not in {"pan", "torrent"}:
        errors.append("channel must be 'pan' or 'torrent'")
    if not isinstance(getattr(adapter, "priority", None), int):
        errors.append("priority must be an integer")

    search_method = getattr(adapter, "search", None)
    if not callable(search_method):
        errors.append("search() must be implemented")
    else:
        params = list(inspect.signature(search_method).parameters)
        expected = ["query", "intent", "limit", "page", "http_client"]
        if params != expected:
            errors.append(f"search() parameters must be {expected}")

    search_result_count = 0
    used_http_client = False
    if run_search and callable(search_method) and not errors:
        client = RecordingHTTPClient()
        intent = SearchIntent(
            query="ubuntu",
            original_query="ubuntu",
            kind="general",
            channel=adapter.channel,
            title_core="ubuntu",
            title_tokens=["ubuntu"],
        )
        try:
            results = adapter.search("ubuntu", intent, limit=1, page=1, http_client=client)
        except Exception as exc:
            errors.append(f"search smoke test failed: {exc}")
            results = []
        used_http_client = client.used
        if not isinstance(results, list):
            errors.append("search() must return list[SearchResult]")
            results = []
        for index, item in enumerate(results):
            if not isinstance(item, SearchResult):
                errors.append(f"result {index} is not SearchResult")
                continue
            for field_name in ("provider", "title", "link_or_magnet"):
                if not str(getattr(item, field_name, "")).strip():
                    errors.append(f"result {index}.{field_name} must be non-empty")
        search_result_count = len(results)
        if not used_http_client and search_result_count:
            errors.append("search returned results without using the provided http_client")

    return {
        "name": getattr(adapter, "name", ""),
        "channel": getattr(adapter, "channel", ""),
        "priority": getattr(adapter, "priority", None),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "search_result_count": search_result_count,
        "used_http_client": used_http_client,
    }


def validate_source_file(path: str | Path, run_search: bool = True) -> dict[str, Any]:
    source_path = Path(path).resolve()
    module, load_error = _load_module(source_path)
    payload: dict[str, Any] = {
        "schema_version": "3",
        "path": str(source_path),
        "valid": False,
        "adapters": [],
        "errors": [],
        "warnings": [],
    }
    if load_error:
        payload["errors"].append(load_error)
        return payload
    adapter_classes = _adapter_classes(module)
    if not adapter_classes:
        payload["errors"].append("no SourceAdapter subclass found")
        return payload
    adapter_payloads: list[dict[str, Any]] = []
    for adapter_class in adapter_classes:
        try:
            adapter = adapter_class()
        except Exception as exc:
            adapter_payloads.append(
                {
                    "name": getattr(adapter_class, "name", ""),
                    "channel": getattr(adapter_class, "channel", ""),
                    "priority": getattr(adapter_class, "priority", None),
                    "valid": False,
                    "errors": [f"adapter initialization failed: {exc}"],
                    "warnings": [],
                    "search_result_count": 0,
                    "used_http_client": False,
                }
            )
            continue
        adapter_payloads.append(_validate_adapter(adapter, run_search=run_search))
    payload["adapters"] = adapter_payloads
    payload["valid"] = all(item["valid"] for item in adapter_payloads)
    return payload


__all__ = ["validate_source_file"]
