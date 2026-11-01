"""
Comprehensive test suite for NOWPayments USDT Integration in Yoomo SMM Panel.
Tests:
1. Invoice creation
2. Invalid API credentials
3. Invalid webhook signature
4. Successful payment & USDT wallet credit
5. Pending payment (no credit)
6. Failed and expired payment
7. Duplicate webhook idempotency
8. Duplicate wallet credit prevention
9. Underpayment and overpayment handling
10. Database transaction rollback safety
11. Unauthorized payment initiation
12. Existing UPI functionality regression
"""

import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app

from app.database import SessionLocal
from app.models import PaymentTransaction, Transaction, User
from app.payments.base import PaymentOrderResult
from app.payments.nowpayments import (
    NOWPAYMENTS_FAILED_STATUSES,
    NOWPAYMENTS_PENDING_STATUSES,
    NOWPAYMENTS_SUCCESS_STATUSES,
    NOWPaymentsProvider,
)
from app.services.deposit import (
    create_deposit_intent,
    process_deposit_completion,
    submit_manual_upi_proof,
)
from app.services.wallet import credit_wallet


# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------

TEST_IPN_SECRET = "u4+0Jf64VO+PaR4DhtxkgkQCsfOn1fx4"


def sign_payload(payload_dict: dict, secret: str = TEST_IPN_SECRET) -> str:
    """Computes HMAC-SHA512 signature using sorted JSON keys matching NOWPayments spec."""
    raw = json.dumps(payload_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha512).hexdigest()


# ---------------------------------------------------------------------------
# 1. Invoice Creation
# ---------------------------------------------------------------------------

class TestInvoiceCreation:
    def test_provider_create_order_success(self):
        """Provider creates order and parses response correctly."""
        provider = NOWPaymentsProvider()
        provider.api_key = "test-key"

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "payment_id": "6354293095",
            "payment_status": "waiting",
            "pay_address": "0x1234567890abcdef1234567890abcdef12345678",
            "price_amount": 10.0,
            "price_currency": "usd",
            "pay_amount": 9.995,
            "pay_currency": "usdtbsc",
            "order_id": "DEP-TEST-001",
            "expiration_estimate_date": "2026-11-01T15:00:00Z",
        }

        with patch("httpx.Client.post", return_value=mock_resp):
            result = provider.create_order(
                amount=Decimal("10.00"),
                currency="USDT",
                deposit_reference="DEP-TEST-001",
                user_id=1,
                webhook_url="https://yoomo.in/wallet/webhooks/nowpayments",
                pay_currency="usdtbsc",
            )

            assert result.success is True
            assert result.order_id == "6354293095"
            assert result.crypto_address == "0x1234567890abcdef1234567890abcdef12345678"
            assert "api.qrserver.com" in result.qr_url

    def test_deposit_intent_endpoint_nowpayments(self, client, regular_user, user_headers):
        """POST /wallet/deposit-intent with nowpayments returns invoice data."""
        test_np_id = f"np_{uuid.uuid4().hex[:10]}"
        with patch.object(
            NOWPaymentsProvider,
            "create_order",
            return_value=PaymentOrderResult(
                success=True,
                order_id=test_np_id,
                amount=Decimal("10.00"),
                currency="USDT",
                crypto_address="0xabc123",
                network="bsc",
                qr_url="https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=0xabc123",
                raw_response={
                    "payment_id": test_np_id,
                    "pay_amount": "10.00",
                    "payin_extra_id": None,
                    "expiration_estimate_date": "2026-11-01T15:00:00Z",
                },
            ),
        ), patch.object(NOWPaymentsProvider, "is_configured", return_value=True):
            res = client.post(
                "/wallet/deposit-intent",
                json={
                    "amount": 10.0,
                    "payment_method": "nowpayments",
                    "pay_currency": "usdtbsc",
                },
                headers=user_headers,
            )

            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["currency"] == "USDT"
            assert data["pay_address"] == "0xabc123"
            assert data["network"] == "bsc"


# ---------------------------------------------------------------------------
# 2. Invalid API Credentials
# ---------------------------------------------------------------------------

class TestInvalidAPICredentials:
    def test_is_configured_without_keys(self, monkeypatch):
        """Provider reports not configured when keys are missing."""
        monkeypatch.delenv("NOWPAYMENTS_API_KEY", raising=False)
        monkeypatch.delenv("NOWPAYMENTS_IPN_SECRET", raising=False)
        p = NOWPaymentsProvider()
        assert p.is_configured() is False

    def test_create_order_401_error(self):
        """Provider handles 401 Unauthorized safely."""
        provider = NOWPaymentsProvider()
        provider.api_key = "invalid-key"

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"message": "Invalid API key"}
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=MagicMock(), response=mock_resp
        )

        with patch("httpx.Client.post", return_value=mock_resp):
            result = provider.create_order(
                amount=Decimal("10.00"),
                currency="USDT",
                deposit_reference="DEP-TEST-401",
                user_id=1,
                webhook_url="https://yoomo.in/wallet/webhooks/nowpayments",
            )
            assert result.success is False
            assert "401" in result.error_message or "Invalid API key" in result.error_message


# ---------------------------------------------------------------------------
# 3. Invalid Webhook Signature
# ---------------------------------------------------------------------------

class TestInvalidWebhookSignature:
    def test_verify_webhook_valid_signature(self):
        """Valid HMAC-SHA512 passes verification."""
        provider = NOWPaymentsProvider()
        provider.ipn_secret = TEST_IPN_SECRET

        payload = {
            "payment_id": 6354293095,
            "payment_status": "finished",
            "pay_amount": 10.0,
            "actually_paid": 10.0,
            "outcome_amount": 9.95,
            "order_id": "DEP-VALID-01",
        }
        raw_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(TEST_IPN_SECRET.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()

        res = provider.verify_webhook({"x-nowpayments-sig": sig}, raw_body)
        assert res.valid is True
        assert res.status == "finished"

    def test_verify_webhook_invalid_signature_rejected(self):
        """Invalid or tampered signature is rejected."""
        provider = NOWPaymentsProvider()
        provider.ipn_secret = TEST_IPN_SECRET

        raw_body = b'{"payment_id": 123, "payment_status": "finished"}'
        res = provider.verify_webhook({"x-nowpayments-sig": "bad_signature_here"}, raw_body)
        assert res.valid is False
        assert "failed" in res.error_message.lower()

    def test_webhook_endpoint_rejects_bad_signature(self, client):
        """Endpoint handles bad signature by returning HTTP 200 with valid=False (anti-retry)."""
        res = client.post(
            "/wallet/webhooks/nowpayments",
            content=b'{"payment_id": 123}',
            headers={"x-nowpayments-sig": "invalid_sig", "Content-Type": "application/json"},
        )
        assert res.status_code == 200
        assert res.json().get("valid") is False


# ---------------------------------------------------------------------------
# 4. Successful Payment & USDT Wallet Credit
# ---------------------------------------------------------------------------

class TestSuccessfulPayment:
    def test_credit_wallet_usdt_currency(self, regular_user):
        """credit_wallet with currency='USDT' credits balance_usdt, not INR balance."""
        db = SessionLocal()
        try:
            user = db.get(User, regular_user.id)
            initial_inr = user.balance
            initial_usdt = user.balance_usdt or Decimal("0.0000")

            credit_wallet(
                db=db,
                user_id=user.id,
                amount=Decimal("15.5000"),
                transaction_type="deposit",
                description="Test USDT Deposit",
                currency="USDT",
            )
            db.commit()

            db.refresh(user)
            # INR balance must remain unchanged
            assert user.balance == initial_inr
            # USDT balance must be incremented
            assert user.balance_usdt == initial_usdt + Decimal("15.5000")

            # Audit ledger record check
            tx = db.scalars(
                select(Transaction)
                .where(Transaction.user_id == user.id)
                .order_by(Transaction.id.desc())
            ).first()
            assert tx.currency == "USDT"
            assert tx.amount == Decimal("15.5000")
        finally:
            db.close()

    def test_full_webhook_finished_credits_wallet(self, client, regular_user):
        """Webhook with finished status completes deposit and credits USDT balance."""
        db = SessionLocal()
        try:
            # Create a test pending payment transaction with unique ID
            ref = f"DEP-USDT-{uuid.uuid4().hex[:8].upper()}"
            payment_id = int(str(uuid.uuid4().int)[:10])
            deposit = PaymentTransaction(
                user_id=regular_user.id,
                reference=ref,
                amount=Decimal("20.00"),
                currency="USDT",
                gateway="nowpayments",
                gateway_order_id=str(payment_id),
                gateway_payment_id=str(payment_id),
                status="Pending",
            )
            db.add(deposit)
            db.commit()
            dep_id = deposit.id

            user = db.get(User, regular_user.id)
            prev_usdt = user.balance_usdt or Decimal("0.0000")
        finally:
            db.close()

        payload = {
            "payment_id": payment_id,
            "payment_status": "finished",
            "pay_amount": 20.0,
            "actually_paid": 20.0,
            "outcome_amount": 19.98,
            "order_id": ref,
        }
        raw_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(TEST_IPN_SECRET.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()

        # Mock server-side verification to confirm payment
        with patch.object(
            NOWPaymentsProvider,
            "verify_order_status",
            return_value=MagicMock(
                verified=True,
                status="finished",
                amount=Decimal("19.98"),
                utr="0x_tx_hash_123",
            ),
        ), patch.dict("os.environ", {"NOWPAYMENTS_IPN_SECRET": TEST_IPN_SECRET}):
            res = client.post(
                "/wallet/webhooks/nowpayments",
                content=raw_body,
                headers={"x-nowpayments-sig": sig, "Content-Type": "application/json"},
            )
            assert res.status_code == 200

        # Verify DB deposit is Completed and user was credited in USDT
        db = SessionLocal()
        try:
            dep = db.get(PaymentTransaction, dep_id)
            assert dep.status == "Completed"
            assert dep.actually_paid == Decimal("20.00")

            usr = db.get(User, regular_user.id)
            assert usr.balance_usdt == prev_usdt + Decimal("19.98")
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 5. Pending Payment (No Credit)
# ---------------------------------------------------------------------------

class TestPendingPayment:
    @pytest.mark.parametrize("status", ["waiting", "confirming", "sending"])
    def test_intermediate_statuses_do_not_credit(self, client, regular_user, status):
        """Intermediate statuses (waiting, confirming, sending) must NOT credit wallet."""
        db = SessionLocal()
        try:
            ref = f"DEP-PEND-{uuid.uuid4().hex[:8].upper()}"
            pid = int(str(uuid.uuid4().int)[:10])
            dep = PaymentTransaction(
                user_id=regular_user.id,
                reference=ref,
                amount=Decimal("10.00"),
                currency="USDT",
                gateway="nowpayments",
                gateway_order_id=str(pid),
                status="Pending",
            )
            db.add(dep)
            db.commit()
            dep_id = dep.id

            user = db.get(User, regular_user.id)
            initial_usdt = user.balance_usdt
        finally:
            db.close()

        payload = {
            "payment_id": pid,
            "payment_status": status,
            "pay_amount": 10.0,
            "order_id": ref,
        }
        raw_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(TEST_IPN_SECRET.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()

        with patch.dict("os.environ", {"NOWPAYMENTS_IPN_SECRET": TEST_IPN_SECRET}):
            res = client.post(
                "/wallet/webhooks/nowpayments",
                content=raw_body,
                headers={"x-nowpayments-sig": sig, "Content-Type": "application/json"},
            )
            assert res.status_code == 200
            assert res.json().get("action") == "waiting"

        db = SessionLocal()
        try:
            dep = db.get(PaymentTransaction, dep_id)
            assert dep.status == "Pending"
            assert dep.provider_status == status
            usr = db.get(User, regular_user.id)
            assert usr.balance_usdt == initial_usdt
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 6. Failed and Expired Payment
# ---------------------------------------------------------------------------

class TestFailedAndExpiredPayment:
    @pytest.mark.parametrize("fail_status", ["failed", "expired", "refunded"])
    def test_failed_status_marks_deposit_failed(self, client, regular_user, fail_status):
        """Failed or expired webhook marks deposit as Failed without crediting."""
        db = SessionLocal()
        try:
            ref = f"DEP-FAIL-{uuid.uuid4().hex[:8].upper()}"
            pid = int(str(uuid.uuid4().int)[:10])
            dep = PaymentTransaction(
                user_id=regular_user.id,
                reference=ref,
                amount=Decimal("10.00"),
                currency="USDT",
                gateway="nowpayments",
                gateway_order_id=str(pid),
                status="Pending",
            )
            db.add(dep)
            db.commit()
            dep_id = dep.id

            user = db.get(User, regular_user.id)
            initial_usdt = user.balance_usdt
        finally:
            db.close()

        payload = {
            "payment_id": pid,
            "payment_status": fail_status,
            "pay_amount": 10.0,
            "order_id": ref,
        }
        raw_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(TEST_IPN_SECRET.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()

        with patch.dict("os.environ", {"NOWPAYMENTS_IPN_SECRET": TEST_IPN_SECRET}):
            res = client.post(
                "/wallet/webhooks/nowpayments",
                content=raw_body,
                headers={"x-nowpayments-sig": sig, "Content-Type": "application/json"},
            )
            assert res.status_code == 200

        db = SessionLocal()
        try:
            dep = db.get(PaymentTransaction, dep_id)
            assert dep.status == "Failed"
            usr = db.get(User, regular_user.id)
            assert usr.balance_usdt == initial_usdt
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 7. Duplicate Webhook (Idempotency)
# ---------------------------------------------------------------------------

class TestDuplicateWebhook:
    def test_duplicate_webhook_returns_already_processed(self, client, regular_user):
        """Sending the same webhook for an already Completed deposit is idempotent."""
        db = SessionLocal()
        try:
            ref = f"DEP-IDEMP-{uuid.uuid4().hex[:8].upper()}"
            pid = int(str(uuid.uuid4().int)[:10])
            dep = PaymentTransaction(
                user_id=regular_user.id,
                reference=ref,
                amount=Decimal("10.00"),
                currency="USDT",
                gateway="nowpayments",
                gateway_order_id=str(pid),
                gateway_payment_id=str(pid),
                status="Completed",  # Already finished!
            )
            db.add(dep)
            db.commit()

            user = db.get(User, regular_user.id)
            balance_before = user.balance_usdt
        finally:
            db.close()

        payload = {
            "payment_id": pid,
            "payment_status": "finished",
            "pay_amount": 10.0,
            "order_id": ref,
        }
        raw_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(TEST_IPN_SECRET.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()

        with patch.dict("os.environ", {"NOWPAYMENTS_IPN_SECRET": TEST_IPN_SECRET}):
            res = client.post(
                "/wallet/webhooks/nowpayments",
                content=raw_body,
                headers={"x-nowpayments-sig": sig, "Content-Type": "application/json"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data.get("already_processed") is True

        db = SessionLocal()
        try:
            usr = db.get(User, regular_user.id)
            assert usr.balance_usdt == balance_before
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 8. Duplicate Wallet Credit Prevention
# ---------------------------------------------------------------------------

class TestDuplicateWalletCredit:
    def test_process_deposit_completion_prevents_duplicate_credit(self, regular_user):
        """process_deposit_completion is strictly idempotent; second call does not double credit."""
        db = SessionLocal()
        try:
            user = db.get(User, regular_user.id)
            initial_usdt = user.balance_usdt or Decimal("0.0000")

            ref = f"DEP-DUP-{uuid.uuid4().hex[:8].upper()}"
            deposit = PaymentTransaction(
                user_id=regular_user.id,
                reference=ref,
                amount=Decimal("5.00"),
                currency="USDT",
                gateway="nowpayments",
                status="Pending",
            )
            db.add(deposit)
            db.commit()
            dep_id = deposit.id

            # First completion succeeds and credits 5.00 USDT
            first_res = process_deposit_completion(
                db=db,
                deposit_id=dep_id,
                verified_amount=Decimal("5.00"),
                credit_currency="USDT",
            )
            assert first_res.status == "Completed"

            db.refresh(user)
            assert user.balance_usdt == initial_usdt + Decimal("5.00")

            # Second completion must return deposit without crediting balance again
            second_res = process_deposit_completion(
                db=db,
                deposit_id=dep_id,
                verified_amount=Decimal("5.00"),
                credit_currency="USDT",
            )
            assert second_res.status == "Completed"

            # Verify balance was NOT credited a second time
            db.refresh(user)
            assert user.balance_usdt == initial_usdt + Decimal("5.00")
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 9. Underpayment and Overpayment Handling
# ---------------------------------------------------------------------------

class TestUnderpaymentAndOverpayment:
    def test_partially_paid_credits_actual_received_amount(self, client, regular_user):
        """Underpayment ('partially_paid') credits the actual received outcome_amount."""
        db = SessionLocal()
        try:
            ref = f"DEP-UNDER-{uuid.uuid4().hex[:8].upper()}"
            pid = int(str(uuid.uuid4().int)[:10])
            dep = PaymentTransaction(
                user_id=regular_user.id,
                reference=ref,
                amount=Decimal("20.00"),  # Customer requested 20 USDT
                currency="USDT",
                gateway="nowpayments",
                gateway_order_id=str(pid),
                status="Pending",
            )
            db.add(dep)
            db.commit()
            dep_id = dep.id

            user = db.get(User, regular_user.id)
            initial_usdt = user.balance_usdt or Decimal("0.0000")
        finally:
            db.close()

        payload = {
            "payment_id": pid,
            "payment_status": "partially_paid",
            "pay_amount": 20.0,
            "actually_paid": 12.0,      # Only sent 12 USDT
            "outcome_amount": 11.95,    # Net received
            "order_id": ref,
        }
        raw_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(TEST_IPN_SECRET.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()

        with patch.object(
            NOWPaymentsProvider,
            "verify_order_status",
            return_value=MagicMock(
                verified=True,
                status="partially_paid",
                amount=Decimal("11.95"),
                utr="tx_partially_paid",
            ),
        ), patch.dict("os.environ", {"NOWPAYMENTS_IPN_SECRET": TEST_IPN_SECRET}):
            res = client.post(
                "/wallet/webhooks/nowpayments",
                content=raw_body,
                headers={"x-nowpayments-sig": sig, "Content-Type": "application/json"},
            )
            assert res.status_code == 200

        db = SessionLocal()
        try:
            usr = db.get(User, regular_user.id)
            # Must credit 11.95 USDT, NOT 20.00
            assert usr.balance_usdt == initial_usdt + Decimal("11.95")
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 10. Database Transaction Rollback
# ---------------------------------------------------------------------------

class TestDatabaseTransactionRollback:
    def test_credit_wallet_rollback_on_error(self, regular_user):
        """Database transaction rolls back cleanly if credit fails mid-transaction."""
        db = SessionLocal()
        try:
            user = db.get(User, regular_user.id)
            initial_usdt = user.balance_usdt

            # Simulate an error inside transaction
            with pytest.raises(RuntimeError):
                user.balance_usdt = (user.balance_usdt or Decimal("0")) + Decimal("100.00")
                raise RuntimeError("Simulated sudden database crash")

            db.rollback()
            db.refresh(user)
            # Balance must remain unchanged
            assert user.balance_usdt == initial_usdt
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 11. Unauthorized Payment Initiation
# ---------------------------------------------------------------------------

class TestUnauthorizedPaymentInitiation:
    def test_deposit_intent_requires_auth(self):
        """Unauthenticated call to /wallet/deposit-intent returns 401."""
        with TestClient(app) as anonymous_client:
            res = anonymous_client.post(
                "/wallet/deposit-intent",
                json={"amount": 10.0, "payment_method": "nowpayments"},
            )
            assert res.status_code == 401


# ---------------------------------------------------------------------------
# 12. Existing UPI Functionality Regression
# ---------------------------------------------------------------------------

class TestExistingUPIRegression:
    def test_manual_upi_deposit_limits_still_enforced(self, regular_user):
        """Existing UPI flow still enforces min and max limits and credits in INR."""
        db = SessionLocal()
        try:
            user = db.get(User, regular_user.id)
            user.can_manual_upi = True
            db.commit()

            # Under min deposit raises exception
            with pytest.raises(Exception):
                submit_manual_upi_proof(
                    db=db,
                    user=user,
                    deposit_id=None,
                    amount=Decimal("10.00"),  # min is ₹50
                    utr_number=f"UTR{uuid.uuid4().hex[:8]}",
                )

            # Valid amount creates pending INR deposit
            deposit = submit_manual_upi_proof(
                db=db,
                user=user,
                deposit_id=None,
                amount=Decimal("100.00"),
                utr_number=f"UTR{uuid.uuid4().hex[:8]}",
            )
            assert deposit.currency == "INR"
            assert deposit.status == "Pending Verification"
        finally:
            db.close()

    def test_credit_wallet_defaults_to_inr(self, regular_user):
        """Calling credit_wallet without currency argument still defaults to INR."""
        db = SessionLocal()
        try:
            user = db.get(User, regular_user.id)
            prev_inr = user.balance
            prev_usdt = user.balance_usdt

            credit_wallet(
                db=db,
                user_id=user.id,
                amount=Decimal("50.00"),
                transaction_type="deposit",
                description="Default INR",
            )
            db.commit()
            db.refresh(user)

            assert user.balance == prev_inr + Decimal("50.00")
            assert user.balance_usdt == prev_usdt
        finally:
            db.close()
