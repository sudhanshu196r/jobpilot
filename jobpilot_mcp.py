"""Entry point for MCP clients: `python /path/to/jobpilot/jobpilot_mcp.py`.

Works from any working directory, which is what Claude Desktop and other
clients use when they launch a server.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
