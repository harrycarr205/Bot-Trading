"""Entry point: python -m tradingsystem.dashboard

Starts the read-only dashboard on Settings.dashboard_host/dashboard_port.
"""

import uvicorn

from tradingsystem.config import Settings
from tradingsystem.dashboard.app import app

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run(app, host=settings.dashboard_host, port=settings.dashboard_port)
