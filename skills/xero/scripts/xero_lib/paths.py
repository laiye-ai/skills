"""Shared cross-platform locations for non-secret local application state."""

from pathlib import Path

from platformdirs import user_data_path


def default_data_dir() -> Path:
    """Return the platform-native data directory used by every local state store."""
    # Compatibility namespace: changing it would orphan existing encrypted tokens.
    return Path(user_data_path("managing-xero-bills", "Laiye"))
