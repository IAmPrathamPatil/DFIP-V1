"""Start the P6 web origin: ``python -m dfip_web``."""

from __future__ import annotations

import uvicorn
from dfip_config.settings import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "dfip_web.app:app",
        host=settings.dfip_web_host,
        port=settings.dfip_web_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
