"""
Base provider adapter interface for SMM panel.
All provider integrations must subclass SmmProviderAdapter.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class ProviderConfig:
    """Configuration for a single provider connection."""
    slug: str           # unique provider identifier, e.g. 'vipsmmpro'
    api_url: str
    api_key: str
    timeout: int = 30        # seconds per HTTP request
    max_retries: int = 3
    rate_limit_rpm: int = 60  # requests per minute (0 = unlimited)


@dataclass
class ProviderService:
    """Normalised service record from any provider."""
    provider_service_id: str   # provider's own ID kept as string for safety
    name: str
    category: str
    service_type: str
    rate: Decimal              # provider's raw cost per 1,000 units
    min_quantity: int
    max_quantity: int
    refill: bool = False
    cancel: bool = False


@dataclass
class ProviderOrderResult:
    """Successful order submission result."""
    provider_order_id: str
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderOrderStatus:
    """Status of a single order at the provider."""
    provider_order_id: str
    status: str
    start_count: int | None = None
    remains: int | None = None
    charge: Decimal | None = None
    error: str | None = None


class SmmProviderAdapter(ABC):
    """Abstract adapter that every SMM provider integration must implement."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @property
    def slug(self) -> str:
        return self.config.slug

    @abstractmethod
    def get_services(self) -> list[ProviderService]:
        """Fetch the full service catalogue from the provider."""

    @abstractmethod
    def get_balance(self) -> Decimal:
        """Return the current account balance at this provider."""

    @abstractmethod
    def add_order(
        self,
        provider_service_id: str,
        link: str,
        quantity: int,
    ) -> ProviderOrderResult:
        """Submit a new order. Raises ProviderError on failure."""

    @abstractmethod
    def get_order_status(self, provider_order_id: str) -> ProviderOrderStatus:
        """Fetch the status for a single order."""

    def get_multiple_order_statuses(
        self, provider_order_ids: list[str]
    ) -> list[ProviderOrderStatus]:
        """Bulk status fetch. Default: serial single calls. Override for batch."""
        return [self.get_order_status(oid) for oid in provider_order_ids]

    @abstractmethod
    def refill_order(self, provider_order_id: str) -> str:
        """Request a refill for an order. Returns the refill ID."""

    @abstractmethod
    def get_refill_status(self, refill_id: str) -> str:
        """Return the current refill status string."""

    @abstractmethod
    def cancel_orders(
        self, provider_order_ids: list[str]
    ) -> dict[str, Any]:
        """Cancel one or more orders. Returns raw provider response."""


class ProviderError(Exception):
    """Raised when a provider returns an error or an HTTP request fails."""

    def __init__(self, message: str, raw_response: dict | None = None) -> None:
        super().__init__(message)
        self.raw_response: dict = raw_response or {}
