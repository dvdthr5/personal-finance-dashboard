from fastapi.testclient import TestClient
from main import app
import pytest
from uuid import uuid4

client = TestClient(app)


@pytest.fixture(scope="module")
def user_id():
    uname = f"ci_user_{uuid4().hex[:8]}"
    email = f"{uname}@example.com"
    password = "testpass123"
    # register
    client.post(
        "/register", json={"username": uname, "email": email, "password": password}
    )
    # login to obtain user_id
    resp = client.post("/login", json={"identifier": uname, "password": password})
    assert resp.status_code == 200, f"login failed: {resp.text}"
    return resp.json()["user_id"]


def test_root():
    """Ensure the API root responds successfully."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data


def test_add_holding_and_get_holdings(user_id):
    """Test adding and retrieving holdings for a user."""

    holding = {"user_id": user_id, "symbol": "AAPL", "quantity": 10, "price": 150.0}

    add_response = client.post("/holding", json=holding)
    assert add_response.status_code == 200

    get_response = client.get("/portfolio", params={"user_id": user_id})
    assert get_response.status_code == 200

    holdings = get_response.json()
    assert isinstance(holdings, list)
    assert any(h["symbol"] == "AAPL" for h in holdings)


def test_update_holding_quantity(user_id):
    """Test updating a holding's quantity."""
    update_payload = {
        "user_id": user_id,
        "symbol": "AAPL",
        "quantity": 20,
        "price": 150.0,
    }

    response = client.post("/holding", json=update_payload)
    assert response.status_code == 200
    assert "updated" in response.json()["message"].lower()


def test_delete_holding(user_id):
    """Test deleting a holding."""
    symbol = "AAPL"
    response = client.delete(f"/holding/{symbol}", params={"user_id": user_id})
    assert response.status_code == 200
    data = response.json()
    assert "deleted" in data["message"].lower()


def test_add_sale_and_get_sales(user_id):
    """Test adding and retrieving sale history."""
    sale = {
        "user_id": user_id,
        "qty": 5,
        "price": 250.0,
        "gain": 100.0,
    }
    symbol = "TSLA"

    add_sale_response = client.post(f"/holding/{symbol}/sell", json=sale)
    assert add_sale_response.status_code == 200

    sales_response = client.get("/sales_history", params={"user_id": user_id})
    assert sales_response.status_code == 200

    sales = sales_response.json()
    assert isinstance(sales, list)
    assert any(s["symbol"] == "TSLA" for s in sales)


def test_get_realized_gains(user_id):
    """Ensure realized gains endpoint works."""
    response = client.get("/realized_gains", params={"user_id": user_id})
    assert response.status_code == 200
    gains = response.json()
    assert isinstance(gains, (float, int))


def test_tax_calculator_estimation(user_id):
    """Simulate tax calculator usage."""
    payload = {"user_id": user_id, "state": "CA", "salary": 75000}

    response = client.post("/tax_calculator", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "short_term" in data
    assert "long_term" in data
