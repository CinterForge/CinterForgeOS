"""linux reusable stage lifecycle utilities.

Platform-specific behavior belongs here when required; application recipes
should call these shared primitives instead of duplicating infrastructure.
"""
from __future__ import annotations
from stage import stage_application
