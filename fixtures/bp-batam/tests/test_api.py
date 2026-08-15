"""Contract-level tests for the dummy BP Batam API."""

from fastapi.testclient import TestClient

from bp_batam_api.main import app


client = TestClient(app)


def test_land_record_is_returned_for_known_nib() -> None:
    response = client.get("/api/v1/land-records/1209260012345")
    assert response.status_code == 200
    assert response.json()["area_square_metres"] == 2400


def test_unknown_nib_returns_not_found() -> None:
    response = client.get("/api/v1/masterlists/0000000000000")
    assert response.status_code == 404


def test_permit_validation_checks_known_plot() -> None:
    response = client.post(
        "/api/v1/permit-validations",
        json={
            "nib": "1209260012345",
            "activity_code": "52101",
            "warehouse_plot": "Kabil Industrial Estate Blok C-4",
        },
    )
    assert response.status_code == 200
    assert response.json()["eligible"] is True
