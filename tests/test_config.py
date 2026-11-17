"""Configuration and credential-loading checks using isolated local fixtures."""

import os
from unittest.mock import patch

import pytest

from scripts.config import load_settings
from scripts.utils import load_environment


def test_external_paths_resolve_from_config_not_working_directory(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    config = repository / "config.toml"
    config.write_text('[paths]\ndata_root = "../external-data"\n[inputs]\n'
                      'services = "raw/services.csv"\nequipment_urls = "raw/urls.csv"\n', encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    settings = load_settings(config, project_root=repository)
    assert settings.data_root == tmp_path / "external-data"
    assert settings.inputs["services"] == tmp_path / "external-data/raw/services.csv"
    assert settings.inputs["coordinate_cache"] is None


def test_data_root_and_inputs_cannot_escape_their_intended_locations(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    config = repository / "config.toml"
    for root in [".", "./data", ".."]:
        config.write_text(f'[paths]\ndata_root = "{root}"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="outside"):
            load_settings(config, project_root=repository)
    config.write_text('[paths]\ndata_root = "../external-data"\n[inputs]\n'
                      'services = "../outside.csv"\nequipment_urls = "raw/urls.csv"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="inside"):
        load_settings(config, project_root=repository)


def test_missing_config_explains_setup(tmp_path):
    with pytest.raises(FileNotFoundError, match="config.example.toml"):
        load_settings(tmp_path / "missing.toml", project_root=tmp_path / "repository")


def test_dotenv_loading_is_explicit_and_preserves_shell_credentials(tmp_path):
    path = tmp_path / ".env"
    path.write_text("CARTA_SOCIAL_TEST_USERNAME=local-user\nCARTA_SOCIAL_TEST_KEY=file-key\n", encoding="utf-8")
    with patch.dict(os.environ, {"CARTA_SOCIAL_TEST_KEY": "shell-key"}):
        load_environment(path)
        assert os.environ["CARTA_SOCIAL_TEST_USERNAME"] == "local-user"
        assert os.environ["CARTA_SOCIAL_TEST_KEY"] == "shell-key"
        load_environment(path, override=True)
        assert os.environ["CARTA_SOCIAL_TEST_KEY"] == "file-key"
