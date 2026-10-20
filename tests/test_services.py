def test_get_services(client):
    res = client.get("/services")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["count"] > 0
    assert len(data["services"]) > 0


def test_get_categories(client):
    res = client.get("/services/categories")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert len(data["categories"]) > 0
    assert "name" in data["categories"][0]
    assert "count" in data["categories"][0]
