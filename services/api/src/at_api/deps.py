"""Dependency-injection providers (Doc 05 section 5.2).

All shared resources are resolved through these providers so that tests can
override them via ``app.dependency_overrides`` without patching module globals.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from at_config import Settings


def get_app_settings(request: Request) -> Settings:
    """Return the Settings instance created during application startup.

    Reading from ``app.state`` rather than calling ``get_settings()`` keeps the
    request path free of module-level singletons and makes profile overrides in
    tests a one-line change.
    """
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
