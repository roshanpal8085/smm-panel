def test_admin_stats(client, admin_headers):
    res = client.get("/admin/stats", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "stats" in data
    assert "total_users" in data["stats"]
    assert "provider_balance" in data["stats"]


def test_admin_forbidden_for_regular_user(client, user_headers):
    res = client.get("/admin/stats", headers=user_headers)
    assert res.status_code == 403


def test_admin_users_list(client, admin_headers):
    res = client.get("/admin/users", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert len(data["users"]) > 0
