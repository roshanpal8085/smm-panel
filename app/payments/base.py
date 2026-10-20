from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional


@dataclass
class PaymentOrderResult:
    success: bool
    order_id: str
    amount: Decimal
    currency: str
    checkout_url: Optional[str] = None
    qr_url: Optional[str] = None
    upi_intent: Optional[str] = None
    crypto_address: Optional[str] = None
    network: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


@dataclass
class WebhookResult:
    valid: bool
    order_id: Optional[str] = None
    provider_transaction_id: Optional[str] = None
    utr: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    payer_info: Optional[str] = None
    raw_payload: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class PaymentVerificationResult:
    verified: bool
    status: str
    provider_order_id: str
    utr: Optional[str] = None
    amount: Optional[Decimal] = None
    raw_response: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class BasePaymentProvider(ABC):
    name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """Returns True if the provider credentials and settings are present."""
        pass

    @abstractmethod
    def create_order(
        self,
        amount: Decimal,
        currency: str,
        deposit_reference: str,
        user_id: int,
        webhook_url: Optional[str] = None,
        redirect_url: Optional[str] = None,
        **kwargs
    ) -> PaymentOrderResult:
        """Initializes a payment order with the external provider."""
        pass

    @abstractmethod
    def verify_webhook(
        self,
        headers: Dict[str, str],
        raw_body: bytes
    ) -> WebhookResult:
        """Verifies the authenticity and signature of an incoming webhook."""
        pass

    @abstractmethod
    def verify_order_status(
        self,
        provider_order_id: str
    ) -> PaymentVerificationResult:
        """Performs server-to-server check of the order status."""
        pass
