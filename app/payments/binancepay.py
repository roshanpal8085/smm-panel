import hashlib
import hmac
import json
import logging
import os
import random
import string
import time
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


class BinancePayProvider(BasePaymentProvider):
    name = "binancepay"

    def __init__(self):
        self.api_key = os.getenv("BINANCE_PAY_API_KEY", "").strip()
        self.secret_key = os.getenv("BINANCE_PAY_SECRET_KEY", "").strip()
        self.merchant_id = os.getenv("BINANCE_PAY_MERCHANT_ID", "").strip()
        self.base_url = os.getenv("BINANCE_PAY_BASE_URL", "https://bpay.binanceapi.com").rstrip("/")
        # Configurable USD/USDT to INR exchange rate for fiat to crypto conversion
        self.usd_to_inr_rate = Decimal(os.getenv("USD_TO_INR_RATE", "88.00"))

    def is_configured(self) -> bool:
        """Returns True if the Binance Pay merchant API credentials are present."""
        return bool(self.api_key and self.secret_key)

    def _generate_nonce(self, length: int = 32) -> str:
        """Generates random 32 alphanumeric string for Binance API headers."""
        chars = string.ascii_letters + string.digits
        return "".join(random.choice(chars) for _ in range(length))

    def _sign_payload(self, timestamp: str, nonce: str, body_str: str) -> str:
        """
        Binance Pay V2 HMAC-SHA512 Signature.
        Payload to sign: timestamp + '\n' + nonce + '\n' + body + '\n'
        Returns uppercase hex string.
        """
        payload = f"{timestamp}\n{nonce}\n{body_str}\n"
        sig = hmac.new(
            self.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha512
        ).hexdigest().upper()
        return sig

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
        """
        Creates a Binance Pay V2 checkout order via Open API.
        Converts INR to USDT if needed, signs request with HMAC-SHA512.
        """
        if not self.is_configured():
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message="Binance Pay merchant credentials (BINANCE_PAY_API_KEY, BINANCE_PAY_SECRET_KEY) are not configured."
            )

        # Convert INR amount to USDT if currency is INR
        if currency.upper() == "INR":
            crypto_amount_usdt = (amount / self.usd_to_inr_rate).quantize(Decimal("0.01"))
            # Binance Pay has minimum order amount (e.g. 0.01 USDT)
            if crypto_amount_usdt < Decimal("0.10"):
                crypto_amount_usdt = Decimal("0.10")
        else:
            crypto_amount_usdt = amount

        body = {
            "env": {
                "terminalType": "WEB"
            },
            "merchantTradeNo": deposit_reference,
            "orderAmount": str(crypto_amount_usdt),
            "currency": "USDT",
            "goods": {
                "goodsType": "02",
                "goodsCategory": "Z000",
                "referenceGoodsId": "wallet_recharge",
                "goodsName": "SMM Prime Wallet Top Up",
                "goodsDetail": f"Account deposit of ₹{amount} (approx. {crypto_amount_usdt} USDT)"
            }
        }

        if redirect_url:
            body["returnUrl"] = redirect_url
            body["cancelUrl"] = redirect_url
        if webhook_url:
            body["webhookUrl"] = webhook_url

        body_str = json.dumps(body, separators=(",", ":"))
        timestamp = str(int(time.time() * 1000))
        nonce = self._generate_nonce()
        signature = self._sign_payload(timestamp, nonce, body_str)

        headers = {
            "Content-Type": "application/json",
            "BinancePay-Timestamp": timestamp,
            "BinancePay-Nonce": nonce,
            "BinancePay-Certificate-SN": self.api_key,
            "BinancePay-Signature": signature,
        }

        url = f"{self.base_url}/binancepay/openapi/v2/order"

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(url, headers=headers, content=body_str)
                res_data = res.json()

            if res.status_code == 200 and res_data.get("status") == "SUCCESS":
                order_data = res_data.get("data", {})
                prepay_id = order_data.get("prepayId", "")
                checkout_url = order_data.get("checkoutUrl") or order_data.get("universalUrl")
                qr_url = order_data.get("qrcodeLink")

                return PaymentOrderResult(
                    success=True,
                    order_id=prepay_id,
                    amount=amount,
                    currency=currency,
                    checkout_url=checkout_url,
                    qr_url=qr_url,
                    raw_response=order_data
                )
            else:
                err_msg = res_data.get("errorMessage") or res_data.get("msg") or f"Binance Pay error code: {res_data.get('code')}"
                logger.error(f"Binance Pay create_order failed: {err_msg} | response: {res_data}")
                return PaymentOrderResult(
                    success=False,
                    order_id="",
                    amount=amount,
                    currency=currency,
                    error_message=f"Binance Pay order creation failed: {err_msg}",
                    raw_response=res_data
                )
        except Exception as e:
            logger.exception(f"Network error calling Binance Pay API: {e}")
            return PaymentOrderResult(
                success=False,
                order_id="",
                amount=amount,
                currency=currency,
                error_message=f"Binance Pay API communication error: {str(e)}"
            )

    def verify_webhook(self, headers: Dict[str, Any], raw_body: bytes) -> WebhookResult:
        """
        Verifies Binance Pay V2 Webhook HMAC-SHA512 signature.
        Payload to verify: timestamp + '\n' + nonce + '\n' + raw_body_str + '\n'
        """
        if not self.is_configured():
            return WebhookResult(valid=False, error_message="Binance Pay credentials not configured")

        # Extract headers (case-insensitive search)
        h_lower = {k.lower(): v for k, v in headers.items()}
        timestamp = h_lower.get("binancepay-timestamp", "")
        nonce = h_lower.get("binancepay-nonce", "")
        signature = h_lower.get("binancepay-signature", "")

        if not timestamp or not nonce or not signature:
            return WebhookResult(valid=False, error_message="Missing required BinancePay signature headers")

        body_str = raw_body.decode("utf-8")
        expected_sig = self._sign_payload(timestamp, nonce, body_str)

        if not hmac.compare_digest(expected_sig.upper(), signature.upper()):
            logger.warning(f"Binance Pay webhook signature mismatch. Expected: {expected_sig}, Received: {signature}")
            return WebhookResult(valid=False, error_message="Invalid Binance Pay webhook signature")

        try:
            payload = json.loads(body_str)
        except Exception as e:
            return WebhookResult(valid=False, error_message=f"Invalid JSON payload: {e}")

        biz_type = payload.get("bizType")
        biz_status = payload.get("bizStatus")
        biz_id = payload.get("bizId")

        # The inner data field can be a JSON string or a dictionary
        data_field = payload.get("data")
        if isinstance(data_field, str):
            try:
                inner_data = json.loads(data_field)
            except Exception:
                inner_data = {}
        elif isinstance(data_field, dict):
            inner_data = data_field
        else:
            inner_data = {}

        merchant_trade_no = inner_data.get("merchantTradeNo") or payload.get("merchantTradeNo")
        total_fee = inner_data.get("totalFee")
        currency = inner_data.get("currency", "USDT")

        amount_val = None
        if total_fee is not None:
            try:
                # Convert back to INR if needed or record crypto amount
                amount_val = Decimal(str(total_fee))
            except Exception:
                pass

        is_completed = (biz_status == "PAY_SUCCESS")

        return WebhookResult(
            valid=True,
            order_id=merchant_trade_no or str(biz_id),
            provider_transaction_id=str(biz_id) if biz_id else None,
            utr=str(biz_id) if biz_id else None,
            amount=amount_val,
            currency=currency,
            status="Completed" if is_completed else biz_status,
            raw_payload=body_str
        )

    def verify_order_status(self, provider_order_id: str) -> PaymentVerificationResult:
        """
        Queries Binance Pay order query endpoint to verify payment status.
        """
        if not self.is_configured():
            return PaymentVerificationResult(
                verified=False,
                status="Failed",
                provider_order_id=provider_order_id,
                error_message="Binance Pay not configured"
            )

        body = {
            "merchantTradeNo": provider_order_id
        }
        body_str = json.dumps(body, separators=(",", ":"))
        timestamp = str(int(time.time() * 1000))
        nonce = self._generate_nonce()
        signature = self._sign_payload(timestamp, nonce, body_str)

        headers = {
            "Content-Type": "application/json",
            "BinancePay-Timestamp": timestamp,
            "BinancePay-Nonce": nonce,
            "BinancePay-Certificate-SN": self.api_key,
            "BinancePay-Signature": signature,
        }

        url = f"{self.base_url}/binancepay/openapi/v2/order/query"

        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.post(url, headers=headers, content=body_str)
                res_data = res.json()

            if res.status_code == 200 and res_data.get("status") == "SUCCESS":
                order_data = res_data.get("data", {})
                pay_status = order_data.get("status")
                verified = (pay_status == "PAID")
                amount = None
                order_amount = order_data.get("orderAmount")
                if order_amount:
                    amount = Decimal(str(order_amount))

                return PaymentVerificationResult(
                    verified=verified,
                    status="Completed" if verified else pay_status,
                    provider_order_id=provider_order_id,
                    utr=str(order_data.get("transactionId", "")),
                    amount=amount,
                    raw_response=order_data
                )
            else:
                return PaymentVerificationResult(
                    verified=False,
                    status="Unknown",
                    provider_order_id=provider_order_id,
                    error_message=res_data.get("errorMessage", "Query failed"),
                    raw_response=res_data
                )
        except Exception as e:
            return PaymentVerificationResult(
                verified=False,
                status="Error",
                provider_order_id=provider_order_id,
                error_message=str(e)
            )
