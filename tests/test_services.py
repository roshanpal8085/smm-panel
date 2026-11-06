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


def test_service_description_endpoints(client):
    res = client.get("/services")
    assert res.status_code == 200
    services = res.json()["services"]
    assert len(services) > 0
    first_id = services[0]["id"]

    # Verify /services/{id} includes description key
    detail_res = client.get(f"/services/{first_id}")
    assert detail_res.status_code == 200
    assert "description" in detail_res.json()["service"]

    # Verify /services/{id}/description endpoint
    desc_res = client.get(f"/services/{first_id}/description")
    assert desc_res.status_code == 200
    desc_data = desc_res.json()
    assert desc_data["success"] is True
    assert "description" in desc_data
