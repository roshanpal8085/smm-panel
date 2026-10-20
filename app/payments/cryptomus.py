import base64
import hashlib
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


class CryptomusProvider(BasePaymentProvider):
    name = "cryptomus"

    def __init__(self):
        self.api_key = os.getenv("CRYPTOMUS_API_KEY", "").strip()
        self.merchant_id = os.getenv("CRYPTOMUS_MERCHANT_ID", "").strip()
        self.base_url = os.getenv("CRYPTOMUS_BASE_URL", "https://api.cryptomus.com/v1").rstrip("/")
        # Configurable USD to INR rate for crypto conversions
        self.usd_to_inr_rate = Decimal(os.getenv("USD_TO_INR_RATE", "88.00"))

    def is_configured(self) -> bool:
        return bool(self.api_key and self.merchant_id)

    def _generate_signature(self, data_str: str) -> str:
        b64 = base64.b64encode(data_str.encode("utf-8")).decode("utf-8")
        raw = b64 + self.api_key
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def create_order(
        self,
        amount: Decimal,
        currency: str,
        deposit_reference: str,
        user_id: int,
        webhook_url: Optional[str] = None,
        redirect_url: Optional[str] = None,
        to_currency: Optional[str] = "USDT",
        network: Optional[str] = "tron",
        **kwargs
    ) -> PaymentOrderResult:
        if not self.is_configured():
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message="Cryptomus provider credentials (CRYPTOMUS_API_KEY, CRYPTOMUS_MERCHANT_ID) are not configured."
            )

        # Calculate crypto USD amount if input is INR
        if currency.upper() == "INR":
            crypto_amount_usd = (amount / self.usd_to_inr_rate).quantize(Decimal("0.01"))
        else:
            crypto_amount_usd = amount

        payload = {
            "amount": str(crypto_amount_usd),
            "currency": "USD",
            "order_id": deposit_reference,
            "to_currency": to_currency or "USDT",
            "network": network or "tron",
        }
        if webhook_url:
            payload["url_callback"] = webhook_url
        if redirect_url:
            payload["url_return"] = redirect_url

        json_str = json.dumps(payload, separators=(',', ':'))
        signature = self._generate_signature(json_str)

        headers = {
            "merchant": self.merchant_id,
            "sign": signature,
            "Content-Type": "application/json"
        }

        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"{self.base_url}/payment",
                    content=json_str,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()

            result = data.get("result", {})
            if data.get("state") == 0 and result:
                return PaymentOrderResult(
                    success=True,
                    order_id=result.get("order_id", deposit_reference),
                    amount=amount,
                    currency=currency,
                    checkout_url=result.get("url"),
                    crypto_address=result.get("address"),
                    network=result.get("network"),
                    raw_response=data
                )
            else:
                return PaymentOrderResult(
                    success=False,
                    order_id="",
                    amount=amount,
                    currency=currency,
                    error_message=data.get("message", "Cryptomus rejected invoice creation"),
                    raw_response=data
                )

        except Exception as e:
            logger.error(f"Cryptomus payment creation error: {e}")
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=f"Cryptomus provider error: {str(e)}"
            )

    def verify_webhook(
        self,
        headers: Dict[str, str],
        raw_body: bytes
    ) -> WebhookResult:
        if not self.is_configured():
            return WebhookResult(valid=False, error_message="Cryptomus is not configured")

        incoming_sign = headers.get("sign") or headers.get("Sign")
        if not incoming_sign:
            return WebhookResult(valid=False, error_message="Missing Cryptomus sign header")

        # Cryptomus webhook verification algorithm:
        # MD5(base64(raw_body) + api_key)
        try:
            b64_body = base64.b64encode(raw_body).decode("utf-8")
            expected_sign = hashlib.md5((b64_body + self.api_key).encode("utf-8")).hexdigest()

            if expected_sign.lower() != incoming_sign.strip().lower():
                logger.warning("Cryptomus webhook signature mismatch")
                return WebhookResult(valid=False, error_message="Invalid signature")

            data = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            return WebhookResult(valid=False, error_message=f"Invalid payload format: {e}")

        order_id = data.get("order_id")
        status = data.get("status", "").lower()
        payment_amount_usd = data.get("payment_amount_usd")
        tx_hash = data.get("txid")
        payer_currency = data.get("payer_currency")

        if not order_id:
            return WebhookResult(valid=False, error_message="Missing order_id in Cryptomus webhook")

        # Status paid or paid_over means successfully paid
        is_completed = status in ["paid", "paid_over"]

        # Convert USD back to INR if needed
        converted_amount_inr = None
        if payment_amount_usd is not None:
            converted_amount_inr = (Decimal(str(payment_amount_usd)) * self.usd_to_inr_rate).quantize(Decimal("0.01"))

        return WebhookResult(
            valid=True,
            order_id=order_id,
            provider_transaction_id=tx_hash,
            utr=tx_hash,
            amount=converted_amount_inr,
            currency="INR",
            status="Completed" if is_completed else status,
            payer_info=payer_currency,
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
                error_message="Cryptomus is not configured"
            )

        payload = {"order_id": provider_order_id}
        json_str = json.dumps(payload, separators=(',', ':'))
        signature = self._generate_signature(json_str)

        headers = {
            "merchant": self.merchant_id,
            "sign": signature,
            "Content-Type": "application/json"
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(
                    f"{self.base_url}/payment/info",
                    content=json_str,
                    headers=headers
                )
                res.raise_for_status()
                data = res.json()

            result = data.get("result", {})
            status = result.get("status", "").lower()
            verified = status in ["paid", "paid_over"]

            return PaymentVerificationResult(
                verified=verified,
                status="Completed" if verified else status,
                provider_order_id=provider_order_id,
                utr=result.get("txid"),
                raw_response=data
            )
        except Exception as e:
            return PaymentVerificationResult(
                verified=False,
                status="unknown",
                provider_order_id=provider_order_id,
                error_message=str(e)
            )
