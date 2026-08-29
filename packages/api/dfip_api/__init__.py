"""DFIP API package.

P5 working-set HTTP access to existing P3/P4 state, plus P7 publication
routes. P9 adds application-level route authorization. That is not RLS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dfip_api.app import create_app as create_app

__all__ = ["create_app"]
__version__ = "0.5.0"


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from dfip_api.app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
