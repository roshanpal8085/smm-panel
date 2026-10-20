def test_api_v2_balance(client, admin_user):
    res = client.post("/api/v2", data={
        "key": admin_user.api_key,
        "action": "balance"
    })
    assert res.status_code == 200
    data = res.json()
    assert "balance" in data
    assert data["currency"] == "INR"


def test_api_v2_services(client, admin_user):
    res = client.post("/api/v2", data={
        "key": admin_user.api_key,
        "action": "services"
    })
    assert res.status_code == 200
    services = res.json()
    assert isinstance(services, list)
    assert len(services) > 0
    assert "service" in services[0]
    assert "rate" in services[0]


def test_api_v2_invalid_key(client):
    res = client.post("/api/v2", data={
        "key": "invalid_api_key_test_123",
        "action": "balance"
    })
    assert res.status_code == 200
    data = res.json()
    assert "error" in data
