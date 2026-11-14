import json
import logging
import socket
import sys
import urllib.request

import uvicorn

from . import __version__
from .config import config


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _running_jobpilot(host: str, port: int) -> str | None:
    """Version of the JobPilot already on this port, or None if it's something else."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=2) as resp:
            data = json.load(resp)
    except (OSError, ValueError):
        return None
    return data.get("version", "unknown") if isinstance(data, dict) and data.get("ok") else None


def _explain_busy_port(host: str, port: int) -> None:
    kill = (f"netstat -ano | findstr :{port}   (then: taskkill /PID <pid> /F)" if sys.platform == "win32"
            else f"lsof -ti tcp:{port} | xargs kill")
    other = _running_jobpilot(host, port)
    if other == __version__:
        print(f"\n  JobPilot {__version__} is already running at http://{host}:{port} — just open that address.\n")
        return
    if other:
        label = f"An older JobPilot ({other})" if other != "unknown" else "An older JobPilot"
        print(f"\n  {label} is still running on port {port}.\n"
              f"  Stop it, then start again:\n\n      {kill}\n      ./run.sh\n")
    else:
        print(f"\n  Port {port} is used by another program.\n"
              f"  Either stop it ({kill}),\n"
              f"  or run JobPilot on another port: add JOBPILOT_PORT=8766 to the .env file\n"
              f"  (then use http://{host}:8766 in the browser extension too).\n")
    sys.exit(1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not _port_free(config.host, config.port):
        _explain_busy_port(config.host, config.port)
        return
    print(f"\n  JobPilot {__version__} running at http://{config.host}:{config.port}\n")
    uvicorn.run("app.main:app", host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
