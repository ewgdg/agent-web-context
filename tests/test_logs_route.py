from fastapi.testclient import TestClient

from src.agent_web_context.main import app


client = TestClient(app)


def test_logs_root_with_trailing_slash():
    resp = client.get("/logs/")
    assert resp.status_code == 200
    assert "Log Files Browser" in resp.text


def test_logs_root_without_trailing_slash():
    resp = client.get("/logs")
    assert resp.status_code == 200
    assert "Log Files Browser" in resp.text
