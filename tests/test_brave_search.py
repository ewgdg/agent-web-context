from src.agent_web_context.search import (
    BraveSearch,
    GoogleSearch,
    _parse_brave_web_results,
    create_search_client,
)


def test_parse_brave_web_results():
    payload = {
        "web": {
            "results": [
                {
                    "title": "Example",
                    "url": "https://example.com",
                    "description": "An example result.",
                    "extra_snippets": [
                        "Alternate snippet 1.",
                        "Alternate snippet 2.",
                    ],
                }
            ]
        }
    }
    results = _parse_brave_web_results(payload)
    assert len(results) == 1
    assert results[0].title == "Example"
    assert results[0].link == "https://example.com"
    assert "example" in results[0].snippet.lower()
    assert "alternate snippet 1" in results[0].snippet.lower()


def test_create_search_client_prefers_brave_when_key_present(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "test-token")
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    client = create_search_client("hello")
    assert isinstance(client, BraveSearch)


def test_create_search_client_defaults_to_google_without_brave_key(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    client = create_search_client("hello")
    assert isinstance(client, GoogleSearch)
