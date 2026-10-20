def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"


def test_public_pages(client):
    for path in ["/", "/services-list", "/api-docs", "/login", "/register"]:
        res = client.get(path)
        assert res.status_code == 200
        assert "<html" in res.text
