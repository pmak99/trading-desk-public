import pytest
from fastapi.testclient import TestClient
from src.main import app

@pytest.fixture
def client():
    return TestClient(app)

def test_root_health(client):
    """Root endpoint returns health status."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "ivcrush"
    assert data["status"] == "healthy"

def test_health_endpoint(client):
    """Health endpoint returns system status."""
    response = client.get("/api/health?format=json")
    assert response.status_code == 200

def test_dispatch_endpoint(client):
    """Dispatch endpoint accepts POST."""
    response = client.post("/dispatch")
    # Should return 200 even if no job scheduled
    assert response.status_code == 200
