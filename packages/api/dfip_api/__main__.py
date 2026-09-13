"""Start the P5 API with uvicorn: ``python -m dfip_api``."""

from __future__ import annotations

import logging

import uvicorn
from dfip_config.settings import load_settings, normalized_log_level


def main() -> None:
    settings = load_settings()
    level_name = normalized_log_level(settings)
    logging.basicConfig(level=getattr(logging, level_name, logging.INFO))
    uvicorn.run(
        "dfip_api.app:app",
        host=settings.dfip_api_host,
        port=settings.dfip_api_port,
        reload=False,
        log_level=level_name.lower(),
    )


if __name__ == "__main__":
    main()
