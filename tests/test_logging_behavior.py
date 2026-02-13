import os
import time
import logging
from pathlib import Path
from uuid import uuid4

# Importing main will initialize logging handlers per your app config
# - Creates ./logs directory
# - Adds RotatingFileHandler for ERROR level at ./logs/errors.log
import src.agent_web_context.main  # noqa: F401


def test_error_logs_are_written_immediately(tmp_path=None):
    logs_dir = Path("./logs")
    error_log = logs_dir / "errors.log"

    # Ensure clean slate for this test (truncate instead of unlink to keep handler's fd linked)
    try:
        if error_log.exists():
            error_log.write_text("")
        else:
            error_log.parent.mkdir(parents=True, exist_ok=True)
            error_log.touch()
    except Exception:
        # If the file is locked/rotated, continue anyway
        pass

    # Emit a unique error line
    logger = logging.getLogger("test.logging")
    unique_msg = f"test-error-{uuid4()}"
    logger.error(unique_msg)

    # Poll briefly for the log file to be created and contain the message
    # FileHandler flushes on emit, so this should be immediate. We allow a small window for FS sync.
    deadline = time.time() + 2.0
    found = False
    while time.time() < deadline:
        if error_log.exists():
            try:
                content = error_log.read_text(errors="ignore")
                if unique_msg in content:
                    found = True
                    break
            except Exception:
                # In case of rotation or concurrent access, retry
                pass
        time.sleep(0.05)

    assert found, (
        f"ERROR log not found in {error_log} within timeout; unique_msg={unique_msg}"
    )
