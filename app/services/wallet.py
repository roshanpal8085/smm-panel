from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, Transaction


class InsufficientBalanceError(Exception):
    pass


def debit_wallet(
    db: Session,
    user_id: int,
    amount: Decimal,
    description: str
):
    """
    Atomically deduct money from user's wallet.

    Uses PostgreSQL row-level locking so two simultaneous
    orders cannot spend the same wallet balance.
    """

    if amount <= 0:
        raise ValueError("Amount must be greater than zero")

    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
    )

    if not user:
        raise ValueError("User not found")

    if user.balance < amount:
        raise InsufficientBalanceError("Insufficient wallet balance")

    user.balance -= amount

    transaction = Transaction(
        user_id=user.id,
        amount=-amount,
        transaction_type="order",
        status="Completed",
        description=description
    )

    db.add(transaction)
    db.flush()

    return user, transaction


def credit_wallet(
    db: Session,
    user_id: int,
    amount: Decimal,
    transaction_type: str,
    description: str
):
    """
    Atomically add money to user's wallet.

    Uses PostgreSQL row-level locking to prevent
    concurrent wallet update problems.
    """

    if amount <= 0:
        raise ValueError("Amount must be greater than zero")

    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
    )

    if not user:
        raise ValueError("User not found")

    user.balance += amount

    transaction = Transaction(
        user_id=user.id,
        amount=amount,
        transaction_type=transaction_type,
        status="Completed",
        description=description
    )

    db.add(transaction)
    db.flush()

    return user, transaction