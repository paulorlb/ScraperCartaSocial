"""Resolve project paths and write files independently of the host computer."""

import json
from pathlib import Path

from .config import PROJECT_ROOT, load_settings

ROOT = PROJECT_ROOT
SETTINGS = load_settings()
DATA_ROOT = SETTINGS.data_root
INPUTS = SETTINGS.inputs
RAW = DATA_ROOT / "raw"
INTERIM = DATA_ROOT / "interim"
PROCESSED = DATA_ROOT / "processed"


def ensure_data_directories():
    """Create the standard folders within the configured external data root.

    Args:
        None: Uses paths resolved from config.toml.

    Returns:
        Path: Configured data root.
    """
    for path in [RAW, INTERIM, PROCESSED]:
        path.mkdir(parents=True, exist_ok=True)
    return DATA_ROOT


def load_environment(env_path=None, override=False):
    """Load local credentials from the repository-root .env file.

    Args:
        env_path: Optional alternative file path, primarily for isolated tests.
        override: Whether file values replace existing process environment values.
            The default preserves credentials already set in the shell.

    Returns:
        bool: Whether the file supplied any environment variables.
    """
    from dotenv import load_dotenv

    path = Path(env_path) if env_path is not None else ROOT / ".env"
    return load_dotenv(dotenv_path=path, override=override)


def write_json(payload, path):
    """Write JSON through a temporary file in the destination directory.

    Args:
        payload: JSON-serializable object.
        path: Destination path as a string or Path.

    Returns:
        Path: Written destination path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path
