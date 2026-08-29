"""Start the P5 API with uvicorn: ``python -m dfip_api``."""

from __future__ import annotations

import uvicorn
from dfip_config.settings import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "dfip_api.app:app",
        host=settings.dfip_api_host,
        port=settings.dfip_api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
