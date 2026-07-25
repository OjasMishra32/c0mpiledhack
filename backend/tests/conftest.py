import pytest

from app import recovery
from app.integrations import voygr
from app.state import HiveState


@pytest.fixture
def hive_state() -> HiveState:
    return HiveState()


@pytest.fixture(autouse=True)
def _clear_module_globals():
    recovery._DEBOUNCE.clear()
    recovery._LAST_ZONE.clear()
    voygr._call_history.clear()
    yield
    recovery._DEBOUNCE.clear()
    recovery._LAST_ZONE.clear()
    voygr._call_history.clear()
