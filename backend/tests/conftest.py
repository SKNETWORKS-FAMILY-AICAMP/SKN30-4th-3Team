import pytest

from app.services.llm import reset_primary_circuit


@pytest.fixture(autouse=True)
def isolate_llm_circuit_state():
    """Prevent process-local provider failures from leaking between tests."""
    reset_primary_circuit()
    yield
    reset_primary_circuit()
