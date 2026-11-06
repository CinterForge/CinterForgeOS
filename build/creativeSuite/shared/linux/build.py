"""linux reusable build lifecycle utilities.

Platform-specific behavior belongs here when required; application recipes
should call these shared primitives instead of duplicating infrastructure.
"""
from __future__ import annotations
from build import BuildSpec, execute_commands, configure_if_changed, reusable_installation, mark_successful_install
