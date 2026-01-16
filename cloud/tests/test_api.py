import os
import pytest
from fastapi.testclient import TestClient
from src.main import app

# Test API key for authenticated endpoints
TEST_API_KEY = "test-api-key-for-unit-tests"


@pytest.fixture(autouse=True)
def set_test_api_key():
    """Set API key for tests."""
    original = os.environ.get('API_KEY')
    os.environ['API_KEY'] = TEST_API_KEY
    yield
    if original is not None:
        os.environ['API_KEY'] = original
    elif 'API_KEY' in os.environ:
        del os.environ['API_KEY']


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Headers with valid API key."""
    return {"X-API-Key": TEST_API_KEY}


def test_root_health(client):
    """Root endpoint returns health status (no auth required)."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "trading-desk"
    assert data["status"] == "healthy"


def test_health_endpoint(client, auth_headers):
    """Health endpoint returns system status."""
    response = client.get("/api/health?format=json", headers=auth_headers)
    assert response.status_code == 200


def test_health_endpoint_no_auth(client):
    """Health endpoint requires API key."""
    response = client.get("/api/health?format=json")
    assert response.status_code == 401  # Missing API key


def test_dispatch_endpoint(client, auth_headers):
    """Dispatch endpoint accepts POST."""
    response = client.post("/dispatch", headers=auth_headers)
    # Should return 200 even if no job scheduled
    assert response.status_code == 200
