"""
Settings API Router
===================

UI preferences, configuration catalog management, and detailed streamed tests.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import time
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from deeptutor.services.config import get_config_test_runner, get_model_catalog_service
from deeptutor.services.embedding.client import reset_embedding_client
from deeptutor.services.llm.client import reset_llm_client
from deeptutor.services.llm.config import clear_llm_config_cache
from deeptutor.services.path_service import get_path_service

router = APIRouter()

_path_service = get_path_service()
SETTINGS_FILE = _path_service.get_settings_file("interface")

DEFAULT_SIDEBAR_NAV_ORDER = {
    "start": ["/", "/history", "/knowledge", "/notebook"],
    "learnResearch": ["/question", "/solver", "/research", "/co_writer"],
}

DEFAULT_UI_SETTINGS = {
    "theme": "light",
    "language": "en",
    "sidebar_description": "✨ Data Intelligence Lab @ HKU",
    "sidebar_nav_order": DEFAULT_SIDEBAR_NAV_ORDER,
}


class SidebarNavOrder(BaseModel):
    start: List[str]
    learnResearch: List[str]


class UISettings(BaseModel):
    theme: Literal["light", "dark", "glass", "snow"] = "light"
    language: Literal["zh", "en"] = "en"
    sidebar_description: Optional[str] = None
    sidebar_nav_order: Optional[SidebarNavOrder] = None


class ThemeUpdate(BaseModel):
    theme: Literal["light", "dark", "glass", "snow"]


class LanguageUpdate(BaseModel):
    language: Literal["zh", "en"]


class SidebarDescriptionUpdate(BaseModel):
    description: str


class SidebarNavOrderUpdate(BaseModel):
    nav_order: SidebarNavOrder


class CatalogPayload(BaseModel):
    catalog: dict[str, Any]


MASKED_SECRET = "********"
SENSITIVE_ENV_KEYS = {"LLM_API_KEY", "EMBEDDING_API_KEY", "SEARCH_API_KEY"}


def _mask_secret(value: Any) -> str:
    return MASKED_SECRET if str(value or "").strip() else ""


def _is_masked_secret(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and set(text) == {"*"}


def _sanitize_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(catalog)
    services = sanitized.get("services") or {}
    for service in services.values():
        profiles = service.get("profiles") or []
        for profile in profiles:
            if isinstance(profile, dict) and "api_key" in profile:
                profile["api_key"] = _mask_secret(profile.get("api_key"))
    return sanitized


def _sanitize_env(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (_mask_secret(value) if key in SENSITIVE_ENV_KEYS else value)
        for key, value in values.items()
    }


def _restore_masked_catalog_secrets(
    catalog: dict[str, Any],
    persisted_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    restored = deepcopy(catalog)
    current = persisted_catalog or get_model_catalog_service().load()
    current_services = current.get("services") or {}
    restored_services = restored.get("services") or {}

    for service_name, service in restored_services.items():
        profiles = service.get("profiles") or []
        current_profiles = (current_services.get(service_name) or {}).get("profiles") or []
        current_by_id = {
            profile.get("id"): profile
            for profile in current_profiles
            if isinstance(profile, dict) and profile.get("id") is not None
        }
        for index, profile in enumerate(profiles):
            if not isinstance(profile, dict) or not _is_masked_secret(profile.get("api_key")):
                continue
            source = current_by_id.get(profile.get("id"))
            if source is None and index < len(current_profiles):
                source = current_profiles[index]
            profile["api_key"] = str((source or {}).get("api_key") or "")

    return restored


def _sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(event)
    catalog = sanitized.get("catalog")
    if isinstance(catalog, dict):
        sanitized["catalog"] = _sanitize_catalog(catalog)
    env = sanitized.get("env")
    if isinstance(env, dict):
        sanitized["env"] = _sanitize_env(env)
    return sanitized


def _invalidate_runtime_caches() -> None:
    """Force runtime clients/config to pick up the latest saved catalog."""
    clear_llm_config_cache()
    reset_llm_client()
    reset_embedding_client()


def load_ui_settings() -> dict[str, Any]:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as handle:
                saved = json.load(handle)
                return {**DEFAULT_UI_SETTINGS, **saved}
        except Exception:
            pass
    return DEFAULT_UI_SETTINGS.copy()


def save_ui_settings(settings: dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, ensure_ascii=False, indent=2)


def _provider_choices() -> dict[str, list[dict[str, str]]]:
    """Build dropdown options for provider selection, keyed by service type."""
    from deeptutor.services.config.provider_runtime import EMBEDDING_PROVIDERS
    from deeptutor.services.provider_registry import PROVIDERS

    llm = sorted(
        [
            {
                "value": s.name,
                "label": (
                    "Custom (OpenAI API)"
                    if s.name == "custom"
                    else "Custom (Anthropic API)"
                    if s.name == "custom_anthropic"
                    else s.label
                ),
                "base_url": s.default_api_base,
            }
            for s in PROVIDERS
        ],
        key=lambda p: p["label"].lower(),
    )
    embedding = sorted(
        [
            {
                "value": name,
                "label": spec.label,
                "base_url": spec.default_api_base,
                "default_dim": str(spec.default_dim) if spec.default_dim else "",
            }
            for name, spec in EMBEDDING_PROVIDERS.items()
        ],
        key=lambda p: p["label"].lower(),
    )
    search = [
        {"value": "brave", "label": "Brave", "base_url": ""},
        {"value": "tavily", "label": "Tavily", "base_url": ""},
        {"value": "jina", "label": "Jina", "base_url": ""},
        {"value": "searxng", "label": "SearXNG", "base_url": ""},
        {"value": "duckduckgo", "label": "DuckDuckGo", "base_url": ""},
        {"value": "perplexity", "label": "Perplexity", "base_url": ""},
    ]
    return {"llm": llm, "embedding": embedding, "search": search}


@router.get("")
async def get_settings():
    return {
        "ui": load_ui_settings(),
        "catalog": _sanitize_catalog(get_model_catalog_service().load()),
        "providers": _provider_choices(),
    }


@router.get("/catalog")
async def get_catalog():
    return {"catalog": _sanitize_catalog(get_model_catalog_service().load())}


@router.put("/catalog")
async def update_catalog(payload: CatalogPayload):
    persisted_catalog = get_model_catalog_service().load()
    catalog = get_model_catalog_service().save(
        _restore_masked_catalog_secrets(payload.catalog, persisted_catalog)
    )
    _invalidate_runtime_caches()
    return {"catalog": _sanitize_catalog(catalog)}


@router.post("/apply")
async def apply_catalog(payload: CatalogPayload | None = None):
    persisted_catalog = get_model_catalog_service().load()
    catalog = (
        _restore_masked_catalog_secrets(payload.catalog, persisted_catalog)
        if payload is not None
        else persisted_catalog
    )
    rendered = get_model_catalog_service().apply(catalog)
    _invalidate_runtime_caches()
    return {
        "message": "Catalog applied to the active .env configuration.",
        "catalog": _sanitize_catalog(get_model_catalog_service().load()),
        "env": _sanitize_env(rendered),
    }


@router.put("/theme")
async def update_theme(update: ThemeUpdate):
    current_ui = load_ui_settings()
    current_ui["theme"] = update.theme
    save_ui_settings(current_ui)
    return {"theme": update.theme}


@router.put("/language")
async def update_language(update: LanguageUpdate):
    current_ui = load_ui_settings()
    current_ui["language"] = update.language
    save_ui_settings(current_ui)
    return {"language": update.language}


@router.put("/ui")
async def update_ui_settings(update: UISettings):
    current_ui = load_ui_settings()
    current_ui.update(update.model_dump(exclude_none=True))
    save_ui_settings(current_ui)
    return current_ui


@router.post("/reset")
async def reset_settings():
    save_ui_settings(DEFAULT_UI_SETTINGS)
    return DEFAULT_UI_SETTINGS


@router.get("/themes")
async def get_themes():
    return {
        "themes": [
            {"id": "snow", "name": "Snow"},
            {"id": "light", "name": "Light"},
            {"id": "dark", "name": "Dark"},
            {"id": "glass", "name": "Glass"},
        ]
    }


@router.get("/sidebar")
async def get_sidebar_settings():
    current_ui = load_ui_settings()
    return {
        "description": current_ui.get(
            "sidebar_description", DEFAULT_UI_SETTINGS["sidebar_description"]
        ),
        "nav_order": current_ui.get("sidebar_nav_order", DEFAULT_UI_SETTINGS["sidebar_nav_order"]),
    }


@router.put("/sidebar/description")
async def update_sidebar_description(update: SidebarDescriptionUpdate):
    current_ui = load_ui_settings()
    current_ui["sidebar_description"] = update.description
    save_ui_settings(current_ui)
    return {"description": update.description}


@router.put("/sidebar/nav-order")
async def update_sidebar_nav_order(update: SidebarNavOrderUpdate):
    current_ui = load_ui_settings()
    current_ui["sidebar_nav_order"] = update.nav_order.model_dump()
    save_ui_settings(current_ui)
    return {"nav_order": update.nav_order.model_dump()}


@router.post("/tests/{service}/start")
async def start_service_test(service: str, payload: CatalogPayload | None = None):
    persisted_catalog = get_model_catalog_service().load()
    catalog = (
        _restore_masked_catalog_secrets(payload.catalog, persisted_catalog)
        if payload
        else None
    )
    run = get_config_test_runner().start(service, catalog)
    return {"run_id": run.id}


@router.get("/tests/{service}/{run_id}/events")
async def stream_service_test_events(service: str, run_id: str, request: Request):
    runner = get_config_test_runner()
    run = runner.get(run_id)

    async def event_stream():
        sent = 0
        while True:
            if await request.is_disconnected():
                return
            events = run.snapshot(sent)
            if events:
                for event in events:
                    yield f"data: {json.dumps(_sanitize_event(event), ensure_ascii=False)}\n\n"
                sent += len(events)
                if events[-1]["type"] in {"completed", "failed"}:
                    return
            else:
                yield "event: heartbeat\ndata: {}\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/tests/{service}/{run_id}/cancel")
async def cancel_service_test(service: str, run_id: str):
    get_config_test_runner().cancel(run_id)
    return {"message": "Cancelled"}


TOUR_CACHE = _path_service.get_settings_dir() / ".tour_cache.json"


@router.get("/tour/status")
async def tour_status():
    if TOUR_CACHE.exists():
        try:
            cache = json.loads(TOUR_CACHE.read_text(encoding="utf-8"))
            return {
                "active": True,
                "status": cache.get("status", "unknown"),
                "launch_at": cache.get("launch_at"),
                "redirect_at": cache.get("redirect_at"),
            }
        except Exception:
            pass
    return {"active": False, "status": "none", "launch_at": None, "redirect_at": None}


class TourCompletePayload(BaseModel):
    catalog: dict[str, Any] | None = None
    test_results: dict[str, str] | None = None


@router.post("/tour/complete")
async def complete_tour(payload: TourCompletePayload | None = None):
    persisted_catalog = get_model_catalog_service().load()
    catalog = (
        _restore_masked_catalog_secrets(payload.catalog, persisted_catalog)
        if payload and payload.catalog
        else persisted_catalog
    )
    rendered = get_model_catalog_service().apply(catalog)
    _invalidate_runtime_caches()
    now = int(time.time())
    launch_at = now + 3
    redirect_at = now + 5

    if TOUR_CACHE.exists():
        try:
            cache = json.loads(TOUR_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
        cache["status"] = "completed"
        cache["launch_at"] = launch_at
        cache["redirect_at"] = redirect_at
        if payload and payload.test_results:
            cache["test_results"] = payload.test_results
        TOUR_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    return {
        "status": "completed",
        "message": "Configuration saved. DeepTutor will restart shortly.",
        "launch_at": launch_at,
        "redirect_at": redirect_at,
        "env": _sanitize_env(rendered),
    }


@router.post("/tour/reopen")
async def reopen_tour():
    return {
        "message": "Run the terminal setup guide from the project root to re-open the guided setup.",
        "command": "python scripts/start_tour.py",
    }
