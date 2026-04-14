"""Execution backends for slop-studio.

Each backend implements the :class:`Backend` ABC and owns backend-specific
HTTP concerns (auth, path prefixes, response shapes). High-level orchestration
lives alongside the backends until Story 6.2 introduces a router.
"""

from slop_studio.backends.base import Backend

__all__ = ["Backend"]
