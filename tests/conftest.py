import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def match_json() -> dict:
    return json.loads((FIXTURES / "match_8868891396.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def ti2025_main_html() -> str:
    payload = json.loads((FIXTURES / "ti2025_main_event_html.json").read_text(encoding="utf-8"))
    return payload["parse"]["text"]


@pytest.fixture(scope="session")
def ti2025_group_html() -> str:
    payload = json.loads((FIXTURES / "ti2025_group_stage_html.json").read_text(encoding="utf-8"))
    return payload["parse"]["text"]
