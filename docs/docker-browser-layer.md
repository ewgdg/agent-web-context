# Docker browser dependency layer

`container/xorg/Dockerfile` installs browser-related Python dependencies (notably `patchright`) in an early Docker layer to improve build caching.

## Why `requirements-browser.txt` exists

- Docker caching works best when early layers change rarely.
- The full `uv.lock` changes frequently (even for unrelated dependencies).
- `requirements-browser.txt` contains only the **`browser` dependency group** exported as a pinned `requirements.txt`-style file, so the “install browser deps” layer only changes when the browser group changes.

In the Dockerfile, this shows up as:

- `COPY requirements-browser.txt ./`
- `uv pip install -r requirements-browser.txt`
- then `uv run patchright install[-deps] ...` (after `patchright` is available)

## How it stays up to date

This repo includes a pre-commit hook that regenerates `requirements-browser.txt` whenever `pyproject.toml` or `uv.lock` changes:

- Config: `.pre-commit-config.yaml`
- Command:
  - `uv export --only-group browser --format requirements-txt --no-dev > requirements-browser.txt`

If you don’t use pre-commit, you can run the command manually and commit the updated `requirements-browser.txt`.

## Can we replace it with `uv sync --only-group browser`?

Yes, but it trades simplicity for weaker Docker caching.

To run `uv sync` in that early layer, you’d need to `COPY pyproject.toml uv.lock` before the install step, so that layer will be invalidated whenever `uv.lock` changes (even for non-browser deps).

