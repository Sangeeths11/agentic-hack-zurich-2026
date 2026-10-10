import pytest

from familyshield.config import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
