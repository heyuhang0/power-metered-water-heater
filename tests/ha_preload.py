"""Import Home Assistant before other pytest plugins.

HA 2026.11 aliases httpx to httpx2 on import and refuses if httpx is already
loaded; the custom-component test plugin imports respx (and httpx) first.
Loaded with ``-p tests.ha_preload``; harmless when HA is not installed.
"""
try:
    import homeassistant  # noqa: F401
except ImportError:
    pass
