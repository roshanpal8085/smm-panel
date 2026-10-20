import hashlib
import hmac
import json
import logging
import os
from decimal import Decimal
from typing import Any, Dict, Optional
import httpx

from app.payments.base import (
    BasePaymentProvider,
    PaymentOrderResult,
    PaymentVerificationResult,
    WebhookResult,
)

logger = logging.getLogger(__name__)


class FamGatewayProvider(BasePaymentProvider):
    name = "fampay"

    def __init__(self):
        self.api_key = os.getenv("FAMGATEWAY_API_KEY", "").strip()
        self.base_url = os.getenv("FAMGATEWAY_BASE_URL", "https://famgateway.in").rstrip("/")
        self.max_amount = Decimal(os.getenv("MAX_UPI_DEPOSIT", "1000.00"))
        self.min_amount = Decimal(os.getenv("MIN_UPI_DEPOSIT", "50.00"))

    def is_configured(self) -> bool:
        return bool(self.api_key)

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
        if not self.is_configured():
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message="FamGateway provider credentials (FAMGATEWAY_API_KEY) are not configured."
            )

        if currency.upper() != "INR":
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=f"FamGateway only supports INR currency (received {currency})"
            )

        if amount > self.max_amount:
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=f"Maximum allowed transaction limit for UPI is ₹{self.max_amount}"
            )

        if amount < self.min_amount:
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=f"Minimum allowed deposit for UPI is ₹{self.min_amount}"
            )

        payload = {
            "api_key": self.api_key,
            "amount": str(amount),
            "order_id": deposit_reference,
        }
        if webhook_url:
            payload["webhook_url"] = webhook_url
        if redirect_url:
            payload["redirect_url"] = redirect_url

        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"{self.base_url}/api/qr.php",
                    data=payload
                )
                response.raise_for_status()
                data = response.json()

            if data.get("status") == "success" or "order_id" in data:
                return PaymentOrderResult(
                    success=True,
                    order_id=str(data.get("order_id", deposit_reference)),
                    amount=amount,
                    currency="INR",
                    checkout_url=data.get("checkout_url"),
                    qr_url=data.get("qr_url"),
                    upi_intent=data.get("upi_intent"),
                    raw_response=data
                )
            else:
                return PaymentOrderResult(
                    success=False,
                    order_id="",
                    amount=amount,
                    currency=currency,
                    error_message=data.get("error", data.get("message", "Provider rejected order creation")),
                    raw_response=data
                )

        except Exception as e:
            logger.error(f"FamGateway order creation error: {e}")
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=f"Provider request error: {str(e)}"
            )

    def verify_webhook(
        self,
        headers: Dict[str, str],
        raw_body: bytes
    ) -> WebhookResult:
        if not self.is_configured():
            return WebhookResult(valid=False, error_message="Provider is not configured")

        sig_header = (
            headers.get("x-famgateway-signature")
            or headers.get("X-FamGateway-Signature")
            or headers.get("X-FAMGATEWAY-SIGNATURE")
        )

        if not sig_header:
            return WebhookResult(valid=False, error_message="Missing X-FamGateway-Signature header")

        # Cryptographic HMAC-SHA256 verification using master API key
        expected_sig = hmac.new(
            self.api_key.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_sig.lower(), sig_header.strip().lower()):
            logger.warning("FamGateway invalid webhook signature mismatch")
            return WebhookResult(valid=False, error_message="Invalid signature")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            return WebhookResult(valid=False, error_message="Invalid JSON payload")

        order_id = payload.get("order_id")
        status = payload.get("status", "").lower()
        amount_val = payload.get("amount")
        utr = payload.get("utr")
        sender = payload.get("sender_name")
        tx_id = payload.get("transaction_id")

        if not order_id:
            return WebhookResult(valid=False, error_message="Missing order_id in webhook")

        amount = Decimal(str(amount_val)) if amount_val is not None else None

        return WebhookResult(
            valid=True,
            order_id=order_id,
            provider_transaction_id=tx_id,
            utr=utr,
            amount=amount,
            currency="INR",
            status="Completed" if status == "success" else status,
            payer_info=sender,
            raw_payload=raw_body.decode("utf-8", errors="ignore")
        )

    def verify_order_status(
        self,
        provider_order_id: str
    ) -> PaymentVerificationResult:
        if not self.is_configured():
            return PaymentVerificationResult(
                verified=False,
                status="failed",
                provider_order_id=provider_order_id,
                error_message="Provider is not configured"
            )

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.get(
                    f"{self.base_url}/api/verify-order.php",
                    params={
                        "api_key": self.api_key,
                        "order_id": provider_order_id
                    }
                )
                res.raise_for_status()
                data = res.json()

            status = data.get("status", "").lower()
            verified = (status == "success")
            amount = Decimal(str(data["amount"])) if "amount" in data else None

            return PaymentVerificationResult(
                verified=verified,
                status="Completed" if verified else status,
                provider_order_id=provider_order_id,
                utr=data.get("utr"),
                amount=amount,
                raw_response=data
            )
        except Exception as e:
            logger.error(f"FamGateway status verification error: {e}")
            return PaymentVerificationResult(
                verified=False,
                status="unknown",
                provider_order_id=provider_order_id,
                error_message=str(e)
            )
