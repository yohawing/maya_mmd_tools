"""Small helpers for presenter tests that build fake UI objects."""

from unittest.mock import Mock


def attach_mocks(target, names, *, mock_cls=Mock):
    """Attach mock widgets with the given attribute names to a fake view."""
    for name in names:
        setattr(target, name, mock_cls())
