from datetime import datetime
from decimal import Decimal
import logging
import os
import uuid
from typing import Optional
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PaymentTransaction, SystemSetting, User
from app.payments.registry import get_payment_provider
from app.services.wallet import credit_wallet

logger = logging.getLogger(__name__)

# Allowed screenshot extensions and MIME types
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


def get_deposit_limits(db: Session, payment_method: str) -> tuple[Decimal, Decimal]:
    """Returns (min_amount, max_amount) for a given payment method."""
    min_setting = db.scalar(select(SystemSetting.value).where(SystemSetting.key == "min_deposit"))
    min_amount = Decimal(min_setting) if min_setting else Decimal("50.00")

    if payment_method in ["manual_upi", "upi_qr", "fampay"]:
        max_setting = db.scalar(select(SystemSetting.value).where(SystemSetting.key == "max_upi_deposit"))
        max_amount = Decimal(max_setting) if max_setting else Decimal("1000.00")
    else:
        max_amount = Decimal("500000.00")

    return min_amount, max_amount


def create_deposit_intent(
    db: Session,
    user: User,
    amount: Decimal,
    payment_method: str,
    currency: str = "INR"
) -> PaymentTransaction:
    """
    Creates a new Pending deposit record with strict backend limit validation.
    Does NOT credit the wallet.
    """
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Deposit amount must be greater than zero")

    min_amount, max_amount = get_deposit_limits(db, payment_method)

    if amount < min_amount:
        raise HTTPException(status_code=400, detail=f"Minimum deposit amount is ₹{min_amount}")

    if amount > max_amount:
        raise HTTPException(status_code=400, detail=f"Maximum allowed transaction limit for this method is ₹{max_amount}")

    # Check permission for manual UPI
    if payment_method in ["manual_upi", "upi_qr"] and not user.can_manual_upi:
        raise HTTPException(status_code=403, detail="Manual UPI deposits are not enabled for your account. Please contact support.")

    reference = f"DEP-{uuid.uuid4().hex[:12].upper()}"

    deposit = PaymentTransaction(
        user_id=user.id,
        reference=reference,
        gateway=payment_method,
        payment_method=payment_method,
        amount=amount,
        currency=currency.upper(),
        status="Pending",
    )

    db.add(deposit)
    db.commit()
    db.refresh(deposit)

    return deposit


def save_screenshot_file(file: UploadFile) -> str:
    """
    Validates and securely saves an uploaded payment proof screenshot.
    Returns relative stored path.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Invalid file uploaded")

    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Invalid file type {ext}. Only PNG, JPG, and WEBP images are accepted.")

    if file.content_type not in ALLOWED_IMAGE_MIMES:
        raise HTTPException(status_code=400, detail="Invalid image MIME type")

    # Read and check size
    content = file.file.read()
    if len(content) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Screenshot file size exceeds 5MB limit")

    upload_dir = os.path.join("uploads", "screenshots")
    os.makedirs(upload_dir, exist_ok=True)

    filename = f"proof_{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(upload_dir, filename)

    with open(filepath, "wb") as f:
        f.write(content)

    return filepath.replace("\\", "/")


def submit_manual_upi_proof(
    db: Session,
    user: User,
    deposit_id: Optional[int],
    amount: Decimal,
    utr_number: str,
    notes: Optional[str] = None,
    screenshot_file: Optional[UploadFile] = None
) -> PaymentTransaction:
    """
    Handles manual UPI proof submission with UTR/RRN uniqueness check.
    Transitions deposit to 'Pending Verification'.
    """
    if not user.can_manual_upi:
        raise HTTPException(status_code=403, detail="Manual UPI deposits are not enabled for your account.")

    clean_utr = utr_number.strip().upper()
    if len(clean_utr) < 6:
        raise HTTPException(status_code=400, detail="Invalid Transaction ID / UTR number")

    # 1. Enforce unique UTR check
    existing_utr = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.utr_number == clean_utr,
            PaymentTransaction.status.in_(["Pending", "Pending Verification", "Completed"])
        )
    )
    if existing_utr:
        raise HTTPException(status_code=400, detail="This UTR / Transaction ID has already been submitted.")

    # 2. Check limits
    min_amount, max_amount = get_deposit_limits(db, "manual_upi")
    if amount < min_amount or amount > max_amount:
        raise HTTPException(status_code=400, detail=f"Amount must be between ₹{min_amount} and ₹{max_amount}")

    screenshot_path = None
    if screenshot_file:
        screenshot_path = save_screenshot_file(screenshot_file)

    if deposit_id:
        deposit = db.scalar(
            select(PaymentTransaction)
            .where(PaymentTransaction.id == deposit_id, PaymentTransaction.user_id == user.id)
            .with_for_update()
        )
        if not deposit:
            raise HTTPException(status_code=404, detail="Deposit intent not found")
        if deposit.status == "Completed":
            raise HTTPException(status_code=400, detail="Deposit is already completed")

        deposit.amount = amount
        deposit.utr_number = clean_utr
        deposit.notes = notes
        deposit.payment_method = "manual_upi"
        deposit.status = "Pending Verification"
        if screenshot_path:
            deposit.screenshot_path = screenshot_path
    else:
        reference = f"DEP-{uuid.uuid4().hex[:12].upper()}"
        deposit = PaymentTransaction(
            user_id=user.id,
            reference=reference,
            gateway="manual_upi",
            payment_method="manual_upi",
            amount=amount,
            currency="INR",
            status="Pending Verification",
            utr_number=clean_utr,
            notes=notes,
            screenshot_path=screenshot_path
        )
        db.add(deposit)

    db.commit()
    db.refresh(deposit)
    return deposit


def process_deposit_completion(
    db: Session,
    deposit_id: int,
    verified_amount: Optional[Decimal] = None,
    verified_utr: Optional[str] = None,
    admin_user: Optional[User] = None,
    admin_notes: Optional[str] = None,
    provider_tx_id: Optional[str] = None
) -> PaymentTransaction:
    """
    CRITICAL FINANCIAL FUNCTION:
    Atomically verifies, credits wallet, and completes a deposit exactly once.
    Uses PostgreSQL row-level locking (with_for_update) to prevent race conditions.
    """
    deposit = db.scalar(
        select(PaymentTransaction)
        .where(PaymentTransaction.id == deposit_id)
        .with_for_update()
    )

    if not deposit:
        raise HTTPException(status_code=404, detail="Deposit transaction not found")

    # IDEMPOTENCY CHECK: If already completed, return immediately without double crediting
    if deposit.status == "Completed":
        logger.info(f"Deposit #{deposit_id} is already completed. Skipping credit.")
        return deposit

    if deposit.status in ["Rejected", "Failed", "Expired"]:
        raise HTTPException(status_code=400, detail=f"Cannot complete deposit with status '{deposit.status}'")

    credit_amount = verified_amount if verified_amount is not None else deposit.amount

    # Update payment transaction record
    deposit.status = "Completed"
    deposit.completed_at = datetime.utcnow()
    if verified_utr and not deposit.utr_number:
        deposit.utr_number = verified_utr
    if provider_tx_id:
        deposit.gateway_payment_id = provider_tx_id
    if admin_user:
        deposit.admin_id = admin_user.id
        deposit.admin_notes = admin_notes or f"Approved by admin {admin_user.username}"

    # Atomic wallet credit using row-level locking
    description = f"Deposit credit: {deposit.reference}"
    if deposit.utr_number:
        description += f" (UTR: {deposit.utr_number})"

    credit_wallet(
        db=db,
        user_id=deposit.user_id,
        amount=credit_amount,
        transaction_type="deposit",
        description=description
    )

    db.commit()
    db.refresh(deposit)

    logger.info(f"Deposit #{deposit.id} (Ref: {deposit.reference}) successfully completed for ₹{credit_amount}")
    return deposit


def process_deposit_rejection(
    db: Session,
    deposit_id: int,
    admin_user: User,
    reason: str
) -> PaymentTransaction:
    """
    Rejects a pending deposit. Does NOT credit the wallet.
    Records admin audit information.
    """
    deposit = db.scalar(
        select(PaymentTransaction)
        .where(PaymentTransaction.id == deposit_id)
        .with_for_update()
    )

    if not deposit:
        raise HTTPException(status_code=404, detail="Deposit transaction not found")

    if deposit.status == "Completed":
        raise HTTPException(status_code=400, detail="Cannot reject an already completed deposit")

    deposit.status = "Rejected"
    deposit.admin_id = admin_user.id
    deposit.admin_notes = reason
    deposit.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(deposit)

    logger.info(f"Deposit #{deposit.id} rejected by admin {admin_user.username}. Reason: {reason}")
    return deposit
