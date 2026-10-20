from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SupportTicket, TicketMessage, User
from app.routers.auth import get_current_user

router = APIRouter(
    prefix="/tickets",
    tags=["Support Tickets"]
)


class CreateTicketRequest(BaseModel):
    subject: str = Field(min_length=3, max_length=255)
    category: str = Field(default="Order", max_length=50)
    order_id: Optional[int] = None
    priority: str = Field(default="Normal", max_length=20)
    message: str = Field(min_length=5)


class ReplyTicketRequest(BaseModel):
    message: str = Field(min_length=2)


@router.get("")
def list_tickets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    tickets = db.scalars(
        select(SupportTicket)
        .where(SupportTicket.user_id == current_user.id)
        .order_by(SupportTicket.updated_at.desc())
    ).all()

    return {
        "success": True,
        "count": len(tickets),
        "tickets": [
            {
                "id": t.id,
                "subject": t.subject,
                "category": t.category,
                "order_id": t.order_id,
                "status": t.status,
                "priority": t.priority,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
                "message_count": len(t.messages)
            }
            for t in tickets
        ]
    }


@router.post("")
def create_ticket(
    data: CreateTicketRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ticket = SupportTicket(
        user_id=current_user.id,
        subject=data.subject.strip(),
        category=data.category.strip(),
        order_id=data.order_id,
        priority=data.priority.strip(),
        status="Open"
    )

    db.add(ticket)
    db.flush()

    message = TicketMessage(
        ticket_id=ticket.id,
        user_id=current_user.id,
        is_staff=False,
        message=data.message.strip()
    )

    db.add(message)
    db.commit()
    db.refresh(ticket)

    return {
        "success": True,
        "message": "Ticket created successfully",
        "ticket_id": ticket.id
    }


@router.get("/{ticket_id}")
def get_ticket(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ticket = db.scalar(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            (SupportTicket.user_id == current_user.id) | (current_user.is_admin == True)
        )
    )

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    messages = db.scalars(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket.id)
        .order_by(TicketMessage.created_at.asc())
    ).all()

    return {
        "success": True,
        "ticket": {
            "id": ticket.id,
            "user_id": ticket.user_id,
            "username": ticket.user.username if ticket.user else "User",
            "subject": ticket.subject,
            "category": ticket.category,
            "order_id": ticket.order_id,
            "status": ticket.status,
            "priority": ticket.priority,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
            "messages": [
                {
                    "id": m.id,
                    "user_id": m.user_id,
                    "username": m.user.username if m.user else "User",
                    "is_staff": m.is_staff,
                    "message": m.message,
                    "created_at": m.created_at
                }
                for m in messages
            ]
        }
    }


@router.post("/{ticket_id}/reply")
def reply_ticket(
    ticket_id: int,
    data: ReplyTicketRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ticket = db.scalar(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            (SupportTicket.user_id == current_user.id) | (current_user.is_admin == True)
        )
    )

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    message = TicketMessage(
        ticket_id=ticket.id,
        user_id=current_user.id,
        is_staff=current_user.is_admin,
        message=data.message.strip()
    )

    # If user replies, set status back to Open. If admin replies, set to Answered.
    if current_user.is_admin:
        ticket.status = "Answered"
    else:
        ticket.status = "Open"

    ticket.updated_at = datetime.utcnow()

    db.add(message)
    db.commit()

    return {
        "success": True,
        "message": "Reply submitted successfully"
    }


@router.post("/{ticket_id}/close")
def close_ticket(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ticket = db.scalar(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            (SupportTicket.user_id == current_user.id) | (current_user.is_admin == True)
        )
    )

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket.status = "Closed"
    ticket.updated_at = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "message": "Ticket closed successfully"
    }
