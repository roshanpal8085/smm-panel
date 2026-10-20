import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(min_length=6)


class LoginJsonRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


@router.post("/register")
def register(
    data: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db)
):
    existing_username = db.scalar(
        select(User).where(User.username == data.username)
    )
    if existing_username:
        raise HTTPException(
            status_code=400,
            detail="Username already taken"
        )

    existing_email = db.scalar(
        select(User).where(User.email == data.email)
    )
    if existing_email:
        raise HTTPException(
            status_code=400,
            detail="Email address already registered"
        )

    new_user = User(
        username=data.username.strip(),
        email=data.email.strip().lower(),
        password_hash=hash_password(data.password),
        balance=0,
        api_key=secrets.token_hex(32),
        is_active=True,
        is_admin=False
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token(new_user.id)
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        max_age=86400 * 7,
        samesite="lax",
        secure=False  # Set True in production with HTTPS
    )

    return {
        "success": True,
        "message": "Registration successful",
        "user_id": new_user.id,
        "username": new_user.username,
        "access_token": token,
        "token_type": "bearer"
    }


@router.post("/login")
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.scalar(
        select(User).where(
            (User.username == form_data.username) | (User.email == form_data.username)
        )
    )

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )

    access_token = create_access_token(user.id)

    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        max_age=86400 * 7,
        samesite="lax",
        secure=False
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "balance": str(user.balance),
            "is_admin": user.is_admin
        }
    }


@router.post("/login-json")
def login_json(
    data: LoginJsonRequest,
    response: Response,
    db: Session = Depends(get_db)
):
    user = db.scalar(
        select(User).where(
            (User.username == data.username) | (User.email == data.username)
        )
    )

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )

    access_token = create_access_token(user.id)

    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        max_age=86400 * 7,
        samesite="lax",
        secure=False
    )

    return {
        "success": True,
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "balance": str(user.balance),
            "is_admin": user.is_admin
        }
    }


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key="access_token")
    return {
        "success": True,
        "message": "Logged out successfully"
    }


@router.get("/me")
def get_me(
    current_user: User = Depends(get_current_user)
):
    return {
        "success": True,
        "user": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "balance": str(current_user.balance),
            "is_admin": current_user.is_admin,
            "api_key": current_user.api_key,
            "created_at": current_user.created_at
        }
    }


@router.post("/change-password")
def change_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=400,
            detail="Current password is incorrect"
        )

    current_user.password_hash = hash_password(data.new_password)
    db.commit()

    return {
        "success": True,
        "message": "Password updated successfully"
    }


@router.post("/regenerate-api-key")
def regenerate_api_key(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    current_user.api_key = secrets.token_hex(32)
    db.commit()
    db.refresh(current_user)

    return {
        "success": True,
        "api_key": current_user.api_key
    }