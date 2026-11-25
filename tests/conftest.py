"""Test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom integration in every test."""
    return


@pytest.fixture(autouse=True)
def mock_bluetooth(enable_bluetooth):
    """Provide a mocked Bluetooth stack."""
    return
