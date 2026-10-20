import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import User
from app.security import create_access_token


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_user():
    db = SessionLocal()
    user = db.query(User).filter(User.is_admin == True).first()
    db.close()
    return user


@pytest.fixture(scope="session")
def admin_headers(admin_user):
    token = create_access_token(admin_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def regular_user():
    db = SessionLocal()
    user = db.query(User).filter(User.is_admin == False).first()
    db.close()
    return user


@pytest.fixture(scope="session")
def user_headers(regular_user):
    token = create_access_token(regular_user.id)
    return {"Authorization": f"Bearer {token}"}
