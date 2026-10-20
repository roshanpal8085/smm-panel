from datetime import datetime
from decimal import Decimal
import secrets

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def generate_secure_api_key() -> str:
    return secrets.token_hex(32)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        nullable=False
    )

    api_key: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        nullable=True,
        index=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    can_manual_upi: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    orders = relationship(
        "Order",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    transactions = relationship(
        "Transaction",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    payment_transactions = relationship(
        "PaymentTransaction",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="PaymentTransaction.user_id"
    )

    tickets = relationship(
        "SupportTicket",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    ticket_messages = relationship(
        "TicketMessage",
        back_populates="user"
    )


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    provider_service_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    category: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True
    )

    service_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    provider_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False
    )

    selling_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False
    )

    price_override: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True
    )

    min_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    max_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    refill_available: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    cancel_available: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    # Catalog & Availability separation
    provider_name: Mapped[str] = mapped_column(
        String(100),
        default="vipsmmpro",
        nullable=False
    )

    provider_available: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    customer_visible: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    manual_markup_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2),
        nullable=True
    )

    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    orders = relationship(
        "Order",
        back_populates="service"
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id"),
        nullable=False,
        index=True
    )

    provider_order_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True
    )

    link: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    charge: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False
    )

    provider_charge: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True
    )

    # Financial snapshots at time of order creation
    snapshot_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True
    )

    snapshot_provider_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True
    )

    gross_profit: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True
    )

    start_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    remains: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="Pending",
        nullable=False,
        index=True
    )

    refill_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    refill_status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    snapshot_provider_slug: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True
    )

    snapshot_provider_service_id: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    last_sync_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    sync_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    user = relationship(
        "User",
        back_populates="orders"
    )

    service = relationship(
        "Service",
        back_populates="orders"
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False
    )

    transaction_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="Completed",
        nullable=False
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    user = relationship(
        "User",
        back_populates="transactions"
    )


class ProviderSetting(Base):
    __tablename__ = "provider_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    api_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )

    api_key: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    slug: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
        default="vipsmmpro"
    )

    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=30,
        nullable=False
    )

    max_retries: Mapped[int] = mapped_column(
        Integer,
        default=3,
        nullable=False
    )

    rate_limit_rpm: Mapped[int] = mapped_column(
        Integer,
        default=60,
        nullable=False
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    reference: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True
    )

    gateway: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    gateway_order_id: Mapped[str | None] = mapped_column(
        String(150),
        unique=True,
        nullable=True,
        index=True
    )

    gateway_payment_id: Mapped[str | None] = mapped_column(
        String(150),
        unique=True,
        nullable=True,
        index=True
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="INR"
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="Pending"
    )

    utr_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True
    )

    payment_method: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True
    )

    admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True
    )

    admin_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    screenshot_path: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    crypto_currency: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True
    )

    crypto_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8),
        nullable=True
    )

    crypto_address: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    network: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    checkout_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    qr_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    provider_status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    provider_payload: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    user = relationship(
        "User",
        back_populates="payment_transactions",
        foreign_keys=[user_id]
    )



class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    category: Mapped[str] = mapped_column(
        String(50),
        default="General",
        nullable=False
    )

    order_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="Open",
        nullable=False
    )

    priority: Mapped[str] = mapped_column(
        String(20),
        default="Normal",
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    user = relationship(
        "User",
        back_populates="tickets"
    )

    messages = relationship(
        "TicketMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketMessage.created_at"
    )


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.id"),
        nullable=False,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    is_staff: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    ticket = relationship(
        "SupportTicket",
        back_populates="messages"
    )

    user = relationship(
        "User",
        back_populates="ticket_messages"
    )


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    key: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True
    )

    value: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )


class CategoryMargin(Base):
    __tablename__ = "category_margins"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    category: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    markup_percent: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )


class PriceAuditLog(Base):
    __tablename__ = "price_audit_logs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id"),
        nullable=False,
        index=True
    )

    admin_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    old_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False
    )

    new_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False
    )

    change_type: Mapped[str] = mapped_column(
        String(50),
        default="manual_edit",
        nullable=False
    )

    details: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    service = relationship("Service")
    admin = relationship("User")


class ProviderSyncLog(Base):
    """
    Audit log for provider sync operations (service import, order status sync, etc.).
    """
    __tablename__ = "provider_sync_logs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    provider_slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True
    )

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="success"
    )

    records_affected: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )