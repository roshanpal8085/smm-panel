import os
import secrets
from dotenv import load_dotenv
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Graceful fallback to prevent import-time crash on Vercel cold boot
    print("WARNING: DATABASE_URL is not configured in environment! Falling back to temporary SQLite.")
    DATABASE_URL = "sqlite:///./fallback.db"

# Normalize Render/Heroku postgres:// URLs for SQLAlchemy 2.0
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Ensure SSL requirement for remote PostgreSQL instances
if "postgresql://" in DATABASE_URL and "localhost" not in DATABASE_URL and "127.0.0.1" not in DATABASE_URL:
    if "sslmode=" not in DATABASE_URL:
        separator = "&" if "?" in DATABASE_URL else "?"
        DATABASE_URL = f"{DATABASE_URL}{separator}sslmode=require"

# Configure connection pooling for production reliability.
# On Vercel (serverless / ephemeral containers) keep the pool tiny to avoid
# exhausting the PostgreSQL connection limit when many function instances run
# concurrently.  On Render (long-lived process) a small pool is also fine.
_is_serverless = os.getenv("VERCEL") is not None
engine_kwargs = {
    "pool_pre_ping": True,
}

if "sqlite" not in DATABASE_URL.lower():
    if _is_serverless:
        # Serverless: minimal pool — each invocation creates at most 1 connection
        engine_kwargs.update({
            "pool_size": int(os.getenv("DB_POOL_SIZE", "1")),
            "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "2")),
            "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "300")),
            "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "20")),
            "connect_args": {"connect_timeout": 10},
        })
    else:
        # Long-lived process (Render / local): small but persistent pool
        engine_kwargs.update({
            "pool_size": int(os.getenv("DB_POOL_SIZE", "3")),
            "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "5")),
            "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "1800")),
            "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
            "connect_args": {"connect_timeout": 10},
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
        with engine.connect() as conn:
            for statement in migration_statements:
                try:
                    conn.execute(text(statement))
                    conn.commit()
                except Exception:
                    conn.rollback()

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

        # Strict Admin Privilege Enforcement & Auto-provisioning:
        # Owner account: roshanpal25 / roshanpal8085@gmail.com / roshan@8962042946
        admin_username = "roshanpal25"
        admin_email = "roshanpal8085@gmail.com"
        admin_pass = "roshan@8962042946"

        # Demote all other accounts that are not the owner
        db.query(models.User).filter(
            func.lower(models.User.username) != admin_username,
            func.lower(models.User.email) != admin_email
        ).update({"is_admin": False}, synchronize_session=False)

        # Find or create master admin
        master_admin = db.query(models.User).filter(
            (func.lower(models.User.username) == admin_username) | 
            (func.lower(models.User.email) == admin_email)
        ).first()

        if master_admin:
            master_admin.username = admin_username
            master_admin.email = admin_email
            # Only re-hash if password is actually wrong (skip costly bcrypt on every startup)
            from app.security import verify_password, hash_password
            if not master_admin.password_hash or not verify_password(admin_pass, master_admin.password_hash):
                master_admin.password_hash = hash_password(admin_pass)
            master_admin.is_admin = True
            master_admin.is_active = True
            master_admin.can_manual_upi = True
            if not master_admin.api_key:
                master_admin.api_key = secrets.token_hex(32)
        else:
            from app.security import hash_password
            db.add(models.User(
                username=admin_username,
                email=admin_email,
                password_hash=hash_password(admin_pass),
                balance=Decimal("1000.00"),
                api_key=secrets.token_hex(32),
                is_active=True,
                is_admin=True,
                can_manual_upi=True
            ))
        db.commit()

        # Seed or update default vipsmmpro provider
        import os as _os
        _api_url = _os.getenv("PROVIDER_API_URL", "https://vipsmmpro.com/api/v2")
        _api_key = _os.getenv("PROVIDER_API_KEY", "166ca903b0abc7e2ff6c0b4d0b746c6b")

        default_provider = db.query(models.ProviderSetting).filter(
            models.ProviderSetting.slug == "vipsmmpro"
        ).first()
        if not default_provider:
            db.add(models.ProviderSetting(
                name="VipSmmPro",
                slug="vipsmmpro",
                api_url=_api_url,
                api_key=_api_key,
                is_active=True,
                is_default=True,
            ))
            db.commit()
        else:
            if not default_provider.api_key or default_provider.api_key == "configured_via_admin_or_env":
                default_provider.api_key = _api_key
            default_provider.is_active = True
            db.commit()

        # Seed or update smmxpert provider
        _xpert_url = _os.getenv("SMMXPERT_API_URL", "https://smmxpert.com/api/v2")
        _xpert_key = _os.getenv("SMMXPERT_API_KEY", "f9eda9b4501d6eea1414e1931e7d63b2")
        smmxpert_provider = db.query(models.ProviderSetting).filter(
            models.ProviderSetting.slug == "smmxpert"
        ).first()
        if not smmxpert_provider:
            db.add(models.ProviderSetting(
                name="SMMXpert",
                slug="smmxpert",
                api_url=_xpert_url,
                api_key=_xpert_key,
                is_active=True,
                is_default=False,
            ))
            db.commit()
        else:
            if not smmxpert_provider.api_key or smmxpert_provider.api_key == "configured_via_admin_or_env":
                smmxpert_provider.api_key = _xpert_key
            smmxpert_provider.is_active = True
            db.commit()