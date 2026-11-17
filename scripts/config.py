"""Read machine-specific data paths without loading or exposing credentials."""

from dataclasses import dataclass
from pathlib import Path
import tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    """Resolved external data root and configured inputs for one machine."""

    data_root: Path
    inputs: dict


def load_settings(config_path=None, project_root=PROJECT_ROOT):
    """Resolve data paths from a local TOML file, independently of the cwd.

    Args:
        config_path: Configuration file path; defaults to root config.toml.
        project_root: Repository path, used to reject data stored inside it.

    Returns:
        Settings: External data root and input paths or worksheet names.

    Raises:
        FileNotFoundError: Configuration is absent; copy config.example.toml first.
        ValueError: The root is empty, inside the repository, or an input escapes
            the configured data root.
    """
    project_root = Path(project_root).resolve()
    path = Path(config_path) if config_path is not None else project_root / "config.toml"
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError("Create config.toml from config.example.toml and set paths.data_root.")
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    value = config.get("paths", {}).get("data_root")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Set paths.data_root to a dedicated external data folder.")
    root = Path(value.strip()).expanduser()
    if not root.is_absolute():
        root = path.parent / root
    root = root.resolve()
    if root == project_root or project_root in root.parents or root in project_root.parents:
        raise ValueError("The data root must be a dedicated folder outside the repository.")
    configured = config.get("inputs", {})
    inputs = {}
    for name in ["services", "equipment_urls", "url_text", "coordinate_cache", "selection_workbook"]:
        value = configured.get(name, "")
        if not isinstance(value, str):
            raise ValueError(f"inputs.{name} must be a path string.")
        if not value.strip():
            inputs[name] = None
            continue
        candidate = Path(value.strip()).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if root not in candidate.parents:
            raise ValueError(f"inputs.{name} must point to a file inside the configured data root.")
        inputs[name] = candidate
    for name, default in [("service_sheet", "Services"), ("address_sheet", "Equipment addresses")]:
        value = configured.get(name, default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"inputs.{name} must be a non-empty worksheet name.")
        inputs[name] = value
    for name in ["services", "equipment_urls"]:
        if inputs[name] is None:
            raise ValueError(f"Set the required input path inputs.{name}.")
    return Settings(data_root=root, inputs=inputs)
