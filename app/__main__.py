import logging

import uvicorn

from .config import config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"\n  JobPilot running at http://{config.host}:{config.port}\n")
    uvicorn.run("app.main:app", host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
