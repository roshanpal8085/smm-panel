import uuid
from decimal import Decimal
from app.database import SessionLocal
from app.services.wallet import credit_wallet, debit_wallet, InsufficientBalanceError


def test_wallet_balance_and_deposit_submission(client, user_headers):
    # 1. Get balance
    res = client.get("/wallet/balance", headers=user_headers)
    assert res.status_code == 200
    assert res.json()["success"] is True

    # 2. Submit manual deposit via multipart form (new endpoint)
    utr = f"UTR{uuid.uuid4().hex[:10].upper()}"
    dep_res = client.post(
        "/wallet/manual-deposit",
        headers=user_headers,
        data={
            "amount": "250",
            "utr_number": utr,
            "notes": "Test payment verification",
        }
    )
    assert dep_res.status_code == 200, dep_res.text
    body = dep_res.json()
    assert body["success"] is True
    assert body["status"] == "Pending Verification"

    # 3. Check deposits history
    list_res = client.get("/wallet/deposits", headers=user_headers)
    assert list_res.status_code == 200
    assert list_res.json()["count"] > 0



def test_atomic_wallet_operations(regular_user):
    db = SessionLocal()
    # Credit
    user, tx = credit_wallet(
        db=db,
        user_id=regular_user.id,
        amount=Decimal("100.00"),
        transaction_type="test_credit",
        description="Testing credit"
    )
    db.commit()
    assert tx.amount == Decimal("100.00")

    # Debit
    user, tx = debit_wallet(
        db=db,
        user_id=regular_user.id,
        amount=Decimal("50.00"),
        description="Testing debit"
    )
    db.commit()
    assert tx.amount == Decimal("-50.00")

    # Test insufficient balance raises exception
    try:
        debit_wallet(
            db=db,
            user_id=regular_user.id,
            amount=Decimal("99999999.00"),
            description="Testing overdraft"
        )
        assert False, "Should have raised InsufficientBalanceError"
    except InsufficientBalanceError:
        pass

    db.close()
