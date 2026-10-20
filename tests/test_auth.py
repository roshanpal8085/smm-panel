import uuid


def test_register_and_login(client):
    test_uname = f"test_{uuid.uuid4().hex[:8]}"
    test_email = f"{test_uname}@example.com"
    test_pass = "SecurePass123!"

    # 1. Register
    reg_res = client.post("/auth/register", json={
        "username": test_uname,
        "email": test_email,
        "password": test_pass
    })
    assert reg_res.status_code == 200
    assert reg_res.json()["success"] is True

    # 2. Login JSON
    login_res = client.post("/auth/login-json", json={
        "username": test_uname,
        "password": test_pass
    })
    assert login_res.status_code == 200
    data = login_res.json()
    assert data["success"] is True
    assert "access_token" in data

    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Get /auth/me
    me_res = client.get("/auth/me", headers=headers)
    assert me_res.status_code == 200
    assert me_res.json()["user"]["username"] == test_uname

    # 4. Regenerate API Key
    regen_res = client.post("/auth/regenerate-api-key", headers=headers)
    assert regen_res.status_code == 200
    assert "api_key" in regen_res.json()


def test_invalid_login(client):
    res = client.post("/auth/login-json", json={
        "username": "nonexistent_user_12345",
        "password": "wrong_password"
    })
    assert res.status_code == 401
