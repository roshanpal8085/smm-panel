from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.models import SystemSetting, User
from app.payments.base import BasePaymentProvider
from app.payments.cryptomus import CryptomusProvider
from app.payments.fampay import FamGatewayProvider
from app.payments.binancepay import BinancePayProvider

_PROVIDERS: Dict[str, BasePaymentProvider] = {
    "fampay": FamGatewayProvider(),
    "cryptomus": CryptomusProvider(),
    "binancepay": BinancePayProvider(),
}


def get_payment_provider(name: str) -> Optional[BasePaymentProvider]:
    return _PROVIDERS.get(name.lower())


def get_available_payment_methods(user: User, db: Session) -> List[dict]:
    """
    Returns the list of active payment methods based on system configuration
    and individual user permissions (e.g. can_manual_upi).
    """
    upi_setting = db.query(SystemSetting).filter(SystemSetting.key == "upi_id").first()
    upi_id = upi_setting.value if upi_setting else "roshanpal25@slc"

    min_dep_setting = db.query(SystemSetting).filter(SystemSetting.key == "min_deposit").first()
    min_deposit = min_dep_setting.value if min_dep_setting else "50"

    max_upi_setting = db.query(SystemSetting).filter(SystemSetting.key == "max_upi_deposit").first()
    max_upi_deposit = max_upi_setting.value if max_upi_setting else "1000"

    fampay_provider = _PROVIDERS["fampay"]
    cryptomus_provider = _PROVIDERS["cryptomus"]

    methods = []

    # 1. Manual UPI Method (Controlled by user.can_manual_upi)
    if user.can_manual_upi:
        methods.append({
            "id": "manual_upi",
            "name": "UPI — Manual Verification",
            "type": "manual_upi",
            "is_enabled": True,
            "min_amount": min_deposit,
            "max_amount": max_upi_deposit,
            "currency": "INR",
            "upi_id": upi_id,
            "requires_utr": True,
            "supports_screenshot": True,
            "description": f"Pay using Google Pay, PhonePe, Paytm, or CRED to UPI ID: {upi_id}. Submit 12-digit UTR/RRN for verification."
        })

    # 2. Dynamic Automated UPI (FamGateway)
    methods.append({
        "id": "fampay",
        "name": "UPI — Dynamic Instant QR (FamGateway)",
        "type": "dynamic_upi",
        "is_enabled": fampay_provider.is_configured(),
        "min_amount": min_deposit,
        "max_amount": max_upi_deposit,
        "currency": "INR",
        "requires_utr": False,
        "supports_screenshot": False,
        "description": "Generates a unique dynamic QR code per order for instant automated credit." if fampay_provider.is_configured() else "Awaiting merchant API credentials activation in server configuration."
    })

    # 3. Cryptocurrency (Cryptomus)
    methods.append({
        "id": "cryptomus",
        "name": "Cryptocurrency (USDT / Crypto)",
        "type": "crypto",
        "is_enabled": cryptomus_provider.is_configured(),
        "min_amount": min_deposit,
        "max_amount": "500000",
        "currency": "INR",
        "requires_utr": False,
        "supports_screenshot": False,
        "description": "Pay with USDT (TRC-20), BTC, or other cryptocurrencies with automated on-chain confirmation." if cryptomus_provider.is_configured() else "Awaiting merchant API credentials activation in server configuration."
    })

    # 4. Binance Pay (Instant Crypto / USDT)
    binance_provider = _PROVIDERS["binancepay"]
    methods.append({
        "id": "binancepay",
        "name": "Binance Pay — Instant Crypto (USDT)",
        "type": "binancepay",
        "is_enabled": binance_provider.is_configured(),
        "min_amount": min_deposit,
        "max_amount": "500000",
        "currency": "INR",
        "requires_utr": False,
        "supports_screenshot": False,
        "description": "Pay with Binance App or Binance Web via QR code or direct checkout. Instant automatic confirmation." if binance_provider.is_configured() else "Awaiting Binance Pay merchant API credentials activation in server configuration."
    })

    # 5. Payeer & Payoneer (Reported as disabled as required by prompt)
    methods.append({
        "id": "payeer",
        "name": "Payeer",
        "type": "gateway",
        "is_enabled": False,
        "min_amount": min_deposit,
        "max_amount": "50000",
        "currency": "USD",
        "requires_utr": False,
        "supports_screenshot": False,
        "description": "Merchant API currently disabled pending business account eligibility verification."
    })

    methods.append({
        "id": "payoneer",
        "name": "Payoneer",
        "type": "gateway",
        "is_enabled": False,
        "min_amount": "500",
        "max_amount": "200000",
        "currency": "USD",
        "requires_utr": False,
        "supports_screenshot": False,
        "description": "Commercial API unavailable for instant automated wallet settlements; disabled."
    })

    return methods
