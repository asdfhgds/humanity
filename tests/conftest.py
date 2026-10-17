from pathlib import Path

import pytest


@pytest.fixture
def isolated_publication_paths(tmp_path, monkeypatch):
    """Keep publisher integration tests from writing into the checkout."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "config_dir", Path(__file__).resolve().parents[1] / "config")
    monkeypatch.chdir(tmp_path)
    return tmp_path
