"""
Wallet router — handles balance, deposits, webhooks, and manual UPI uploads.
All actual wallet credit logic lives in app/services/deposit.py via
process_deposit_completion() which uses row-level locking for exactly-once
guarantee.
"""
import json
import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PaymentTransaction, SystemSetting, Transaction, User
from app.payments.fampay import FamGatewayProvider
from app.payments.cryptomus import CryptomusProvider
from app.payments.binancepay import BinancePayProvider
from app.payments.nowpayments import NOWPaymentsProvider
from app.payments.registry import get_payment_provider, get_available_payment_methods
from app.routers.auth import get_current_user
from app.services.deposit import (
    create_deposit_intent,
    process_deposit_completion,
    submit_manual_upi_proof,
)
from app.services.wallet import credit_wallet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wallet", tags=["Wallet"])


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class DepositIntentRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    payment_method: str = Field(min_length=2, max_length=50)
    pay_currency: Optional[str] = Field(default="usdtbsc", max_length=30)  # for NOWPayments


class ManualDepositRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    payment_method: str = Field(min_length=2, max_length=50)
    utr_number: str = Field(min_length=4, max_length=100)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# GET /wallet/balance
# ---------------------------------------------------------------------------

@router.get("/balance")
def get_wallet_balance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    currency = db.scalar(
        select(SystemSetting.value).where(SystemSetting.key == "currency")
    ) or "INR"

    return {
        "success": True,
        "balance": str(current_user.balance),
        "balance_usdt": str(current_user.balance_usdt),
        "currency": currency,
    }


# ---------------------------------------------------------------------------
# GET /wallet/transactions
# ---------------------------------------------------------------------------

@router.get("/transactions")
def get_wallet_transactions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    transactions = db.scalars(
        select(Transaction)
        .where(Transaction.user_id == current_user.id)
        .order_by(Transaction.created_at.desc())
    ).all()

    return {
        "success": True,
        "count": len(transactions),
        "transactions": [
            {
                "id": t.id,
                "amount": str(t.amount),
                "transaction_type": t.transaction_type,
                "status": t.status,
                "description": t.description,
                "created_at": t.created_at,
            }
            for t in transactions
        ],
    }


# ---------------------------------------------------------------------------
# GET /wallet/payment-methods
# ---------------------------------------------------------------------------

@router.get("/payment-methods")
def get_payment_methods(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    methods = get_available_payment_methods(user=current_user, db=db)
    return {"success": True, "methods": methods}


# ---------------------------------------------------------------------------
# POST /wallet/deposit-intent
# Creates a Pending deposit and, for automated providers, initiates the order.
# ---------------------------------------------------------------------------

@router.post("/deposit-intent")
def create_deposit(
    data: DepositIntentRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    deposit = create_deposit_intent(
        db=db,
        user=current_user,
        amount=data.amount,
        payment_method=data.payment_method,
        currency="USDT" if data.payment_method == "nowpayments" else "INR",
    )

    response = {
        "success": True,
        "deposit_id": deposit.id,
        "reference": deposit.reference,
        "amount": str(deposit.amount),
        "currency": deposit.currency,
        "payment_method": deposit.payment_method,
        "status": deposit.status,
    }

    # --- FamGateway dynamic QR ---
    if data.payment_method == "fampay":
        provider = FamGatewayProvider()
        if provider.is_configured():
            base_url = str(request.base_url).rstrip("/")
            result = provider.create_order(
                amount=deposit.amount,
                currency="INR",
                deposit_reference=deposit.reference,
                user_id=current_user.id,
                webhook_url=f"{base_url}/wallet/webhooks/fampay",
                redirect_url=f"{base_url}/add-funds?ref={deposit.reference}",
            )
            if result.success:
                deposit.gateway_order_id = result.order_id
                deposit.checkout_url = result.checkout_url
                deposit.qr_url = result.qr_url
                db.commit()
                response["checkout_url"] = result.checkout_url
                response["qr_url"] = result.qr_url
                response["provider_order_id"] = result.order_id
            else:
                # Mark deposit as Failed and surface the error cleanly
                deposit.status = "Failed"
                deposit.admin_notes = result.error_message
                db.commit()
                raise HTTPException(status_code=502, detail=result.error_message)
        else:
            deposit.status = "Failed"
            deposit.admin_notes = "FamGateway API key not configured"
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="FamGateway payment provider is not configured. Please contact support."
            )

    # --- Cryptomus ---
    elif data.payment_method == "cryptomus":
        provider = CryptomusProvider()
        if provider.is_configured():
            base_url = str(request.base_url).rstrip("/")
            result = provider.create_order(
                amount=deposit.amount,
                currency="INR",
                deposit_reference=deposit.reference,
                user_id=current_user.id,
                webhook_url=f"{base_url}/wallet/webhooks/cryptomus",
                redirect_url=f"{base_url}/add-funds?ref={deposit.reference}",
            )
            if result.success:
                deposit.gateway_order_id = result.order_id
                deposit.checkout_url = result.checkout_url
                db.commit()
                response["checkout_url"] = result.checkout_url
                response["provider_order_id"] = result.order_id
            else:
                deposit.status = "Failed"
                deposit.admin_notes = result.error_message
                db.commit()
                raise HTTPException(status_code=502, detail=result.error_message)
        else:
            deposit.status = "Failed"
            deposit.admin_notes = "Cryptomus credentials not configured"
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="Cryptocurrency payment provider is not configured. Please contact support."
            )

    # --- Binance Pay ---
    elif data.payment_method == "binancepay":
        provider = BinancePayProvider()
        if provider.is_configured():
            base_url = str(request.base_url).rstrip("/")
            result = provider.create_order(
                amount=deposit.amount,
                currency="INR",
                deposit_reference=deposit.reference,
                user_id=current_user.id,
                webhook_url=f"{base_url}/wallet/webhooks/binancepay",
                redirect_url=f"{base_url}/add-funds?ref={deposit.reference}",
            )
            if result.success:
                deposit.gateway_order_id = result.order_id
                deposit.checkout_url = result.checkout_url
                deposit.qr_url = result.qr_url
                db.commit()
                response["checkout_url"] = result.checkout_url
                response["qr_url"] = result.qr_url
                response["provider_order_id"] = result.order_id
            else:
                deposit.status = "Failed"
                deposit.admin_notes = result.error_message
                db.commit()
                raise HTTPException(status_code=502, detail=result.error_message)
        else:
            deposit.status = "Failed"
            deposit.admin_notes = "Binance Pay credentials not configured"
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="Binance Pay is not configured. Please contact support or configure API credentials."
            )

    # --- NOWPayments USDT ---
    elif data.payment_method == "nowpayments":
        provider = NOWPaymentsProvider()
        if not provider.is_configured():
            deposit.status = "Failed"
            deposit.admin_notes = "NOWPayments credentials not configured"
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="USDT payment provider is not configured. Please contact support."
            )

        # Parse pay_currency from request body (extra field)
        pay_currency = getattr(data, "pay_currency", None) or "usdtbsc"

        base_url = str(request.base_url).rstrip("/")
        ipn_url = f"https://yoomo.in/wallet/webhooks/nowpayments"  # must be public
        # Fallback for non-production environments
        if "localhost" in base_url or "127.0.0.1" in base_url:
            ipn_url = f"{base_url}/wallet/webhooks/nowpayments"

        result = provider.create_order(
            amount=deposit.amount,       # USDT amount
            currency="USDT",
            deposit_reference=deposit.reference,
            user_id=current_user.id,
            webhook_url=ipn_url,
            redirect_url=f"https://yoomo.in/add-funds?ref={deposit.reference}",
            pay_currency=pay_currency,
        )

        if result.success:
            raw = result.raw_response or {}
            deposit.gateway_order_id = result.order_id     # NOWPayments payment_id
            deposit.gateway_payment_id = result.order_id
            deposit.crypto_address = result.crypto_address
            deposit.network = result.network
            deposit.qr_url = result.qr_url
            deposit.currency = "USDT"
            deposit.crypto_currency = pay_currency
            deposit.crypto_amount = Decimal(str(raw.get("pay_amount", deposit.amount)))
            deposit.provider_payload = json.dumps(raw)
            db.commit()

            response.update({
                "currency": "USDT",
                "pay_currency": pay_currency,
                "pay_address": result.crypto_address,
                "pay_amount": str(raw.get("pay_amount", deposit.amount)),
                "payin_extra_id": raw.get("payin_extra_id"),  # memo/tag for some chains
                "network": result.network,
                "qr_url": result.qr_url,
                "provider_payment_id": result.order_id,
                "expires_at": raw.get("expiration_estimate_date") or raw.get("valid_until"),
                "nowpayments_status": "waiting",
            })
        else:
            deposit.status = "Failed"
            deposit.admin_notes = result.error_message
            db.commit()
            raise HTTPException(status_code=502, detail=result.error_message)

    return response


# ---------------------------------------------------------------------------
# POST /wallet/manual-deposit  (multipart form with optional screenshot)
# Accepts: amount (form), utr_number (form), notes (form), screenshot (file)
# ---------------------------------------------------------------------------

@router.post("/manual-deposit")
async def submit_manual_deposit(
    amount: Decimal = Form(..., gt=0),
    utr_number: str = Form(..., min_length=4, max_length=100),
    notes: Optional[str] = Form(None),
    screenshot: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    deposit = submit_manual_upi_proof(
        db=db,
        user=current_user,
        deposit_id=None,
        amount=amount,
        utr_number=utr_number,
        notes=notes,
        screenshot_file=screenshot,
    )

    return {
        "success": True,
        "message": "Payment submitted. Your funds will be credited after admin verification.",
        "deposit_id": deposit.id,
        "reference": deposit.reference,
        "amount": str(deposit.amount),
        "utr_number": deposit.utr_number,
        "status": deposit.status,
    }


# ---------------------------------------------------------------------------
# GET /wallet/deposits  — user's own deposit history
# ---------------------------------------------------------------------------

@router.get("/deposits")
def get_deposits(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    payments = db.scalars(
        select(PaymentTransaction)
        .where(PaymentTransaction.user_id == current_user.id)
        .order_by(PaymentTransaction.created_at.desc())
    ).all()

    return {
        "success": True,
        "count": len(payments),
        "deposits": [
            {
                "id": p.id,
                "reference": p.reference,
                "payment_method": p.payment_method or p.gateway or "UPI",
                "utr_number": p.utr_number,
                "amount": str(p.amount),
                "currency": p.currency,
                "status": p.status,
                "notes": p.notes,
                "checkout_url": p.checkout_url,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
                "completed_at": p.completed_at,
            }
            for p in payments
        ],
    }


# ---------------------------------------------------------------------------
# GET /wallet/deposits/{deposit_id}/status — server-side status check
# ---------------------------------------------------------------------------

@router.get("/deposits/{deposit_id}/status")
def check_deposit_status(
    deposit_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    deposit = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.id == deposit_id,
            PaymentTransaction.user_id == current_user.id,
        )
    )
    if not deposit:
        raise HTTPException(status_code=404, detail="Deposit not found")

    # For automated providers: poll server-side status if still Pending
    if deposit.status == "Pending" and deposit.gateway in ["fampay", "cryptomus", "binancepay"]:
        provider = get_payment_provider(deposit.gateway)
        query_id = deposit.reference if deposit.gateway == "binancepay" else deposit.gateway_order_id
        if provider and query_id:
            verification = provider.verify_order_status(query_id)
            if verification.verified and deposit.status != "Completed":
                try:
                    deposit = process_deposit_completion(
                        db=db,
                        deposit_id=deposit.id,
                        verified_amount=deposit.amount if deposit.gateway == "binancepay" else verification.amount,
                        verified_utr=verification.utr,
                        provider_tx_id=verification.utr,
                    )
                except HTTPException:
                    pass  # Already completed or failed — that's fine

    # NOWPayments: server-side poll for USDT status
    elif deposit.status == "Pending" and deposit.gateway == "nowpayments":
        provider = NOWPaymentsProvider()
        payment_id = deposit.gateway_order_id or deposit.gateway_payment_id
        if provider.is_configured() and payment_id:
            verification = provider.verify_order_status(payment_id)
            # Update stored provider_status
            deposit.provider_status = verification.status
            db.commit()
            if verification.verified and deposit.status != "Completed":
                try:
                    deposit = process_deposit_completion(
                        db=db,
                        deposit_id=deposit.id,
                        verified_amount=verification.amount,
                        verified_utr=verification.utr,
                        provider_tx_id=payment_id,
                        credit_currency="USDT",
                    )
                except HTTPException:
                    pass
            elif provider.is_failed_status(verification.status):
                deposit.status = "Failed"
                deposit.provider_status = verification.status
                db.commit()

    return {
        "success": True,
        "deposit_id": deposit.id,
        "reference": deposit.reference,
        "status": deposit.status,
        "provider_status": deposit.provider_status,
        "amount": str(deposit.amount),
        "currency": deposit.currency or "INR",
        "pay_address": deposit.crypto_address,
        "network": deposit.network,
        "qr_url": deposit.qr_url,
        "completed_at": deposit.completed_at,
    }


# ---------------------------------------------------------------------------
# POST /wallet/webhooks/fampay
# ---------------------------------------------------------------------------

@router.post("/webhooks/fampay")
async def fampay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives FamGateway payment notifications.
    Verifies HMAC-SHA256 signature using the raw request body.
    Credits wallet exactly once using row-level locking.
    """
    raw_body = await request.body()
    headers = dict(request.headers)

    provider = FamGatewayProvider()
    result = provider.verify_webhook(headers=headers, raw_body=raw_body)

    if not result.valid:
        logger.warning(f"FamGateway webhook rejected: {result.error_message}")
        raise HTTPException(status_code=400, detail=result.error_message or "Invalid webhook")

    # Only process successful payments
    if result.status not in ["Completed", "success"]:
        logger.info(f"FamGateway webhook received non-success status: {result.status}")
        return {"received": True, "status": result.status}

    if not result.order_id:
        raise HTTPException(status_code=400, detail="Missing order_id in webhook payload")

    # Look up deposit by gateway_order_id OR reference
    deposit = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.gateway_order_id == result.order_id
        )
    )
    if not deposit:
        deposit = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.reference == result.order_id
            )
        )
    if not deposit:
        logger.error(f"FamGateway webhook: deposit not found for order_id={result.order_id}")
        raise HTTPException(status_code=404, detail="Deposit record not found")

    # Idempotent: already completed is not an error
    if deposit.status == "Completed":
        return {"received": True, "already_processed": True}

    process_deposit_completion(
        db=db,
        deposit_id=deposit.id,
        verified_amount=result.amount,
        verified_utr=result.utr,
        provider_tx_id=result.provider_transaction_id,
    )

    return {"received": True, "credited": True}


# ---------------------------------------------------------------------------
# POST /wallet/webhooks/cryptomus
# ---------------------------------------------------------------------------

@router.post("/webhooks/cryptomus")
async def cryptomus_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives Cryptomus payment notifications.
    Verifies MD5/base64 signature per Cryptomus docs.
    Credits wallet exactly once.
    """
    raw_body = await request.body()
    headers = dict(request.headers)

    provider = CryptomusProvider()
    result = provider.verify_webhook(headers=headers, raw_body=raw_body)

    if not result.valid:
        logger.warning(f"Cryptomus webhook rejected: {result.error_message}")
        raise HTTPException(status_code=400, detail=result.error_message or "Invalid webhook")

    if result.status not in ["Completed", "paid"]:
        logger.info(f"Cryptomus webhook non-final status: {result.status}")
        return {"received": True, "status": result.status}

    if not result.order_id:
        raise HTTPException(status_code=400, detail="Missing order_id in webhook")

    deposit = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.gateway_order_id == result.order_id
        )
    )
    if not deposit:
        deposit = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.reference == result.order_id
            )
        )
    if not deposit:
        logger.error(f"Cryptomus webhook: deposit not found for order_id={result.order_id}")
        raise HTTPException(status_code=404, detail="Deposit record not found")

    if deposit.status == "Completed":
        return {"received": True, "already_processed": True}

    process_deposit_completion(
        db=db,
        deposit_id=deposit.id,
        verified_amount=result.amount,
        provider_tx_id=result.provider_transaction_id,
    )

    return {"received": True, "credited": True}


# ---------------------------------------------------------------------------
# POST /wallet/webhooks/binancepay
# ---------------------------------------------------------------------------

@router.post("/webhooks/binancepay")
async def binancepay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives Binance Pay payment notifications (V2).
    Verifies HMAC-SHA512 signature using raw request body and BinancePay headers.
    Credits wallet exactly once.
    Returns standard Binance Pay response: {"returnCode": "SUCCESS", "returnMessage": null}
    """
    raw_body = await request.body()
    headers = dict(request.headers)

    provider = BinancePayProvider()
    result = provider.verify_webhook(headers=headers, raw_body=raw_body)

    if not result.valid:
        logger.warning(f"Binance Pay webhook rejected: {result.error_message}")
        return {"returnCode": "FAIL", "returnMessage": result.error_message or "Invalid signature"}

    if result.status not in ["Completed", "PAY_SUCCESS"]:
        logger.info(f"Binance Pay webhook received non-success status: {result.status}")
        return {"returnCode": "SUCCESS", "returnMessage": None}

    if not result.order_id:
        return {"returnCode": "FAIL", "returnMessage": "Missing order_id in webhook"}

    deposit = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.reference == result.order_id
        )
    )
    if not deposit:
        deposit = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.gateway_order_id == result.order_id
            )
        )
    if not deposit:
        logger.error(f"Binance Pay webhook: deposit not found for order_id={result.order_id}")
        return {"returnCode": "FAIL", "returnMessage": "Deposit record not found"}

    if deposit.status == "Completed":
        return {"returnCode": "SUCCESS", "returnMessage": None}

    process_deposit_completion(
        db=db,
        deposit_id=deposit.id,
        verified_amount=deposit.amount,
        verified_utr=result.utr,
        provider_tx_id=result.provider_transaction_id,
    )

    return {"returnCode": "SUCCESS", "returnMessage": None}


# ---------------------------------------------------------------------------
# POST /wallet/webhooks/nowpayments  — NOWPayments IPN
# ---------------------------------------------------------------------------

@router.post("/webhooks/nowpayments")
async def nowpayments_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives NOWPayments IPN (Instant Payment Notification) callbacks.

    Security:
      - Verifies HMAC-SHA512 of sorted JSON body against x-nowpayments-sig header
      - Uses constant-time comparison (hmac.compare_digest)
      - Idempotent: returns 200 immediately if already completed
      - Prevents double-credit via PostgreSQL row-level locking in process_deposit_completion()
      - Validates payment_status is an accepted FINAL status before crediting
      - Credits USDT wallet (balance_usdt), NOT INR wallet (balance)

    Always returns HTTP 200 to prevent NOWPayments retry storms.
    """
    raw_body = await request.body()
    headers = dict(request.headers)

    provider = NOWPaymentsProvider()

    if not provider.is_configured():
        logger.error("NOWPayments webhook received but provider not configured")
        return {"received": False, "error": "Provider not configured"}

    result = provider.verify_webhook(headers=headers, raw_body=raw_body)

    if not result.valid:
        logger.warning(f"NOWPayments IPN signature invalid: {result.error_message}")
        # Return 200 anyway — returning 4xx causes NOWPayments to retry indefinitely
        return {"received": True, "valid": False, "error": result.error_message}

    payment_status = result.status or ""
    payment_id = result.provider_transaction_id  # NOWPayments payment_id
    order_id = result.order_id                   # Our internal reference (DEP-XXXX)

    logger.info(
        f"NOWPayments IPN received: payment_id={payment_id} "
        f"order_id={order_id} status={payment_status}"
    )

    # Store the raw payload for audit, even for non-final statuses
    # Update provider_status in DB for UI polling
    if order_id:
        deposit_record = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.reference == order_id
            )
        )
        if deposit_record and deposit_record.status != "Completed":
            deposit_record.provider_status = payment_status
            if result.raw_payload:
                deposit_record.provider_payload = result.raw_payload
            db.commit()

    # Only proceed to credit on FINAL success statuses
    if not provider.is_success_status(payment_status):
        if provider.is_failed_status(payment_status):
            logger.info(f"NOWPayments payment {payment_id} failed/expired: {payment_status}")
            if deposit_record:
                deposit_record.status = "Failed"
                deposit_record.provider_status = payment_status
                db.commit()
        else:
            logger.info(f"NOWPayments IPN intermediate status={payment_status}. Waiting.")
        return {"received": True, "status": payment_status, "action": "waiting"}

    if not order_id:
        logger.error(f"NOWPayments IPN missing order_id. payment_id={payment_id}")
        return {"received": True, "error": "Missing order_id"}

    # Look up deposit by our internal reference
    deposit = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.reference == order_id
        )
    )
    if not deposit:
        # Fallback: try by gateway_order_id (NOWPayments payment_id)
        deposit = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.gateway_order_id == str(payment_id)
            )
        )
    if not deposit:
        logger.error(
            f"NOWPayments IPN: deposit not found for "
            f"order_id={order_id} payment_id={payment_id}"
        )
        return {"received": True, "error": "Deposit not found"}

    # Idempotent: already credited
    if deposit.status == "Completed":
        logger.info(f"NOWPayments IPN: deposit #{deposit.id} already completed. Skipping.")
        return {"received": True, "already_processed": True}

    # Reject if this payment_id is already linked to ANOTHER deposit (replay protection)
    if payment_id:
        existing = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.gateway_payment_id == str(payment_id),
                PaymentTransaction.id != deposit.id,
                PaymentTransaction.status == "Completed",
            )
        )
        if existing:
            logger.warning(
                f"NOWPayments replay: payment_id {payment_id} already credited "
                f"on deposit #{existing.id}"
            )
            return {"received": True, "error": "Replay detected"}

    # SERVER-SIDE VERIFICATION: never trust webhook amount alone
    # Fetch fresh status from NOWPayments API before crediting
    if payment_id:
        verification = provider.verify_order_status(str(payment_id))
        if not verification.verified:
            logger.warning(
                f"NOWPayments server-side verify failed for payment_id={payment_id}: "
                f"status={verification.status}"
            )
            # Update status but do NOT credit
            deposit.provider_status = verification.status
            db.commit()
            return {"received": True, "error": "Server-side verification failed", "status": verification.status}
        credit_amount = verification.amount or result.amount
    else:
        credit_amount = result.amount

    if not credit_amount or credit_amount <= 0:
        logger.error(f"NOWPayments IPN: zero/invalid credit_amount for payment_id={payment_id}")
        return {"received": True, "error": "Invalid credit amount"}

    # Record payment audit details from webhook payload
    if result.raw_payload:
        try:
            raw_p = json.loads(result.raw_payload) if isinstance(result.raw_payload, str) else result.raw_payload
            if raw_p.get("actually_paid") is not None:
                deposit.actually_paid = Decimal(str(raw_p.get("actually_paid")))
            if raw_p.get("outcome_amount") is not None:
                deposit.outcome_amount = Decimal(str(raw_p.get("outcome_amount")))
            if raw_p.get("pay_currency"):
                deposit.pay_currency = str(raw_p.get("pay_currency"))
            if raw_p.get("payin_hash"):
                deposit.tx_hash = str(raw_p.get("payin_hash"))
            deposit.provider_status = payment_status
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to record audit fields from raw_payload: {e}")

    # Credit USDT wallet with row-level locking
    try:
        process_deposit_completion(
            db=db,
            deposit_id=deposit.id,
            verified_amount=credit_amount,
            verified_utr=result.utr,
            provider_tx_id=str(payment_id) if payment_id else None,
            credit_currency="USDT",
        )
        logger.info(
            f"NOWPayments: USDT wallet credited {credit_amount} USDT "
            f"for user #{deposit.user_id} (deposit #{deposit.id})"
        )
    except HTTPException as exc:
        logger.error(f"NOWPayments IPN credit error: {exc.detail}")
        return {"received": True, "error": exc.detail}
    except Exception as exc:
        logger.exception(f"NOWPayments IPN unexpected error: {exc}")
        return {"received": True, "error": "Internal error during credit"}

    return {"received": True, "credited": True, "amount_usdt": str(credit_amount)}


# ---------------------------------------------------------------------------
# GET /wallet/nowpayments/networks  — return supported USDT networks for UI
# ---------------------------------------------------------------------------

@router.get("/nowpayments/networks")
def get_nowpayments_networks(current_user: User = Depends(get_current_user)):
    """Returns the list of supported USDT networks for NOWPayments deposits."""
    provider = NOWPaymentsProvider()
    return {
        "success": True,
        "configured": provider.is_configured(),
        "networks": provider.get_supported_networks() if provider.is_configured() else [],
    }


# ---------------------------------------------------------------------------
# POST /wallet/dev-credit  (development only — disabled in production)
# ---------------------------------------------------------------------------

@router.post("/dev-credit")
def dev_credit(
    amount: Decimal = Form(Decimal("100.00")),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if os.getenv("ENVIRONMENT", "development").lower() == "production":
        raise HTTPException(status_code=403, detail="Dev endpoint disabled in production")

    credit_wallet(
        db=db,
        user_id=current_user.id,
        amount=amount,
        transaction_type="dev_credit",
        description="Developer test credit"
    )
    db.commit()
    db.refresh(current_user)

    return {"success": True, "new_balance": str(current_user.balance)}