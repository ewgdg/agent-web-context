# Brave Search API

This server can use the Brave Search API as an alternative to Google Custom Search JSON API.

## Configuration

Set an API key in the environment:

- `BRAVE_API_KEY` (preferred)
- `BRAVE_SEARCH_API_KEY` (also supported)

Optionally force the provider selection:

- `SEARCH_PROVIDER=brave` (or `google`)

If `SEARCH_PROVIDER` is not set, the server defaults to:

- **Brave** when `BRAVE_API_KEY`/`BRAVE_SEARCH_API_KEY` is present
- otherwise **Google CSE**

## Notes

- Domain restriction uses the `site:` operator (the same mechanism previously used for Google CSE).
- The implementation uses the Brave **web** endpoint and requests `result_filter=web,query` to avoid extra result types while still receiving the `query` object (count only applies to `web` results).
- The implementation maps:
  - `title` → `SearchResultEntry.title`
  - `url` → `SearchResultEntry.link`
  - `description` (+ `extra_snippets` when available) → `SearchResultEntry.snippet`
