from fastapi.testclient import TestClient
import mongomock

from app import main


def _client_with_db(monkeypatch):
    mock_db = mongomock.MongoClient().db
    monkeypatch.setattr(main, "db", mock_db)
    main.ensure_indexes()
    return TestClient(main.app)


def test_health_ok(monkeypatch):
    client = _client_with_db(monkeypatch)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_employee_and_list(monkeypatch):
    client = _client_with_db(monkeypatch)

    payload = {
        "emp_code": "EMP0001",
        "name": "Asha Rao",
        "email": "asha@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05",
    }

    create_response = client.post("/employees", json=payload)
    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    assert body["emp_code"] == "EMP0001"
    assert body["department"] == "Engineering"
    assert body["joined_on"] == "2026-01-05"
    assert isinstance(body["created_at"], int)

    list_response = client.get("/employees", params={"page": 1, "page_size": 20})
    assert list_response.status_code == 200
    data = list_response.json()
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["items"][0]["emp_code"] == "EMP0001"


def test_create_employee_validates_duplicates_and_bad_shift(monkeypatch):
    client = _client_with_db(monkeypatch)

    payload = {
        "emp_code": "EMP0001",
        "name": "Asha Rao",
        "email": "asha@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-05",
    }

    first = client.post("/employees", json=payload)
    assert first.status_code == 201

    second = client.post("/employees", json=payload)
    assert second.status_code == 409

    invalid = client.post(
        "/employees",
        json={
            **payload,
            "emp_code": "EMP0002",
            "shift_start": "09:30",
            "shift_end": "09:30",
        },
    )
    assert invalid.status_code == 422

    filtered = client.get("/employees", params={"department": "Engineering", "page": 1, "page_size": 20})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
