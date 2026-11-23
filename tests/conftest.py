"""Let the HA test harness find this repo's integration.

The harness points HA's config dir at its own testing_config, whose
`custom_components` package would otherwise hide ours.
"""
from pathlib import Path

import pytest

OURS = str(Path(__file__).resolve().parent.parent / "custom_components")


@pytest.fixture
def our_integration(enable_custom_integrations):
    import custom_components

    if OURS not in list(custom_components.__path__):
        custom_components.__path__.append(OURS)
    yield
