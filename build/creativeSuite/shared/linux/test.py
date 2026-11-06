"""linux reusable test lifecycle utilities.

Platform-specific behavior belongs here when required; application recipes
should call these shared primitives instead of duplicating infrastructure.
"""
from __future__ import annotations
from test import TestSpec, require_installed_application, materialize_test_assets
