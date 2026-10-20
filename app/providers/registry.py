"""
Provider registry: maps provider slugs to adapter classes and provides
a factory that constructs adapters from database ProviderSetting records.

To add a new provider:
1. Create an adapter class that subclasses SmmProviderAdapter in app/providers/
2. Register it here: register_provider("your-slug", YourAdapterClass)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.providers.base import ProviderConfig, SmmProviderAdapter
from app.providers.smmxpert import SmmXpertAdapter
from app.providers.vipsmmpro import VipSmmProAdapter

if TYPE_CHECKING:
    from app.models import ProviderSetting


# ---------------------------------------------------------------------------
# Registry: slug -> adapter class
# Add new provider integrations by registering them here.
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, type[SmmProviderAdapter]] = {
    "vipsmmpro": VipSmmProAdapter,
    "smmxpert": SmmXpertAdapter,
}


def register_provider(slug: str, adapter_class: type[SmmProviderAdapter]) -> None:
    """Register a new provider adapter class under the given slug."""
    _REGISTRY[slug] = adapter_class


def get_adapter_class(slug: str) -> type[SmmProviderAdapter]:
    """Return the adapter class for a slug, or raise KeyError if unknown."""
    if slug not in _REGISTRY:
        raise KeyError(
            f"No adapter registered for provider '{slug}'. "
            f"Registered providers: {list(_REGISTRY)}"
        )
    return _REGISTRY[slug]


def build_adapter(setting: ProviderSetting) -> SmmProviderAdapter:
    """
    Construct a live provider adapter from a ProviderSetting ORM object.
    The adapter class is resolved from the registry via the setting's slug.
    """
    adapter_class = get_adapter_class(setting.slug)
    config = ProviderConfig(
        slug=setting.slug,
        api_url=setting.api_url,
        api_key=setting.api_key,
        timeout=setting.timeout_seconds,
        max_retries=setting.max_retries,
        rate_limit_rpm=setting.rate_limit_rpm,
    )
    return adapter_class(config)


def list_registered_slugs() -> list[str]:
    """Return all known provider slugs in the registry."""
    return list(_REGISTRY.keys())
