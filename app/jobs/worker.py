"""워커 진입점 (`python -m app.jobs.worker`). 실제 구현은 07단계."""

import logging
import sys

from app.config import get_settings
from app.logging import setup_logging


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).error("worker is not implemented yet (step 07)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
