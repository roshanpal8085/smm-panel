import os
import secrets
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not configured")

# Configure connection pooling for production reliability
engine_kwargs = {
    "pool_pre_ping": True,
}

if "sqlite" not in DATABASE_URL.lower():
    engine_kwargs.update({
        "pool_size": int(os.getenv("DB_POOL_SIZE", "15")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "25")),
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "1800")),
    })

engine = create_engine(
    DATABASE_URL,
    **engine_kwargs
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations():
    """
    Ensures newly added columns and tables are created seamlessly
    without needing manual DDL scripts.
    """
    from app import models  # noqa: F401

    # 1. Create all missing tables
    Base.metadata.create_all(bind=engine)

    # 2. Add columns if not existing (for existing PostgreSQL setups)
    if "sqlite" not in DATABASE_URL.lower():
        with engine.begin() as conn:
            migration_statements = [
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS api_key VARCHAR(64) UNIQUE;",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS can_manual_upi BOOLEAN DEFAULT TRUE;",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS utr_number VARCHAR(100);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS payment_method VARCHAR(50);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS notes TEXT;",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS admin_id INTEGER REFERENCES users(id);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS admin_notes TEXT;",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS screenshot_path VARCHAR(255);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS crypto_currency VARCHAR(20);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS crypto_amount NUMERIC(18, 8);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS crypto_address VARCHAR(255);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS network VARCHAR(50);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS checkout_url VARCHAR(500);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS qr_url VARCHAR(500);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS provider_status VARCHAR(50);",
                "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS provider_payload TEXT;",
                # Service catalog & availability
                "ALTER TABLE services ADD COLUMN IF NOT EXISTS provider_name VARCHAR(100) DEFAULT 'vipsmmpro';",
                "ALTER TABLE services ADD COLUMN IF NOT EXISTS provider_available BOOLEAN DEFAULT TRUE;",
                "ALTER TABLE services ADD COLUMN IF NOT EXISTS customer_visible BOOLEAN DEFAULT TRUE;",
                "ALTER TABLE services ADD COLUMN IF NOT EXISTS manual_markup_percent NUMERIC(8, 2);",
                "ALTER TABLE services ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMP;",
                # Order financial snapshot
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS snapshot_rate NUMERIC(12, 4);",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS snapshot_provider_rate NUMERIC(12, 4);",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS gross_profit NUMERIC(12, 4);",
                # Multi-provider support
                "ALTER TABLE provider_settings ADD COLUMN IF NOT EXISTS slug VARCHAR(100) UNIQUE DEFAULT 'vipsmmpro';",
                "ALTER TABLE provider_settings ADD COLUMN IF NOT EXISTS timeout_seconds INTEGER DEFAULT 30;",
                "ALTER TABLE provider_settings ADD COLUMN IF NOT EXISTS max_retries INTEGER DEFAULT 3;",
                "ALTER TABLE provider_settings ADD COLUMN IF NOT EXISTS rate_limit_rpm INTEGER DEFAULT 60;",
                "ALTER TABLE provider_settings ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE;",
                "ALTER TABLE provider_settings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();",
                # Order provider snapshot
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS snapshot_provider_slug VARCHAR(100);",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS snapshot_provider_service_id VARCHAR(50);",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS last_sync_error TEXT;",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS sync_attempts INTEGER DEFAULT 0;",
            ]
            for statement in migration_statements:
                try:
                    conn.execute(text(statement))
                except Exception:
                    pass
            conn.commit()

    # 3. Populate missing API keys for existing users
    with SessionLocal() as db:
        users = db.query(models.User).filter(models.User.api_key.is_(None)).all()
        for user in users:
            user.api_key = secrets.token_hex(32)
        if users:
            db.commit()

        # Seed default settings if empty
        default_settings = {
            "site_name": "SMM PRIME",
            "currency": "INR",
            "currency_symbol": "₹",
            "support_email": "support@smmprime.com",
            "upi_id": os.getenv("UPI_ID", "roshanpal25@slc"),
            "min_deposit": "50",
            "max_upi_deposit": "1000",
            "announcement": "Direct Wholesale Rates & 24/7 Fast Delivery",
            "default_markup_percent": os.getenv("DEFAULT_MARKUP_PERCENT", "50"),
        }
        for key, val in default_settings.items():
            existing = db.query(models.SystemSetting).filter(models.SystemSetting.key == key).first()
            if not existing:
                db.add(models.SystemSetting(key=key, value=val, description=f"Default {key}"))
        db.commit()

        # Seed default vipsmmpro provider if not present
        import os as _os
        default_provider = db.query(models.ProviderSetting).filter(
            models.ProviderSetting.slug == "vipsmmpro"
        ).first()
        if not default_provider:
            _api_url = _os.getenv("PROVIDER_API_URL", "")
            _api_key = _os.getenv("PROVIDER_API_KEY", "")
            if _api_url and _api_key:
                db.add(models.ProviderSetting(
                    name="VipSmmPro",
                    slug="vipsmmpro",
                    api_url=_api_url,
                    api_key=_api_key,
                    is_active=True,
                    is_default=True,
                ))
                db.commit()

        # Seed or update smmxpert provider
        _xpert_url = _os.getenv("SMMXPERT_API_URL", "https://smmxpert.com/api/v2")
        _xpert_key = _os.getenv("SMMXPERT_API_KEY", "")
        smmxpert_provider = db.query(models.ProviderSetting).filter(
            models.ProviderSetting.slug == "smmxpert"
        ).first()
        if not smmxpert_provider:
            db.add(models.ProviderSetting(
                name="SMMXpert",
                slug="smmxpert",
                api_url=_xpert_url,
                api_key=_xpert_key or "configured_via_admin_or_env",
                is_active=bool(_xpert_key),
                is_default=False,
            ))
            db.commit()
        elif _xpert_key:
            smmxpert_provider.api_url = _xpert_url
            smmxpert_provider.api_key = _xpert_key
            smmxpert_provider.is_active = True
            db.commit()