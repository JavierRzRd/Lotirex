"""cPanel: startup file passenger_wsgi.py, application entry point application."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["LOTIREX_SCHEDULER"] = "0"

from backend.wsgi import create_application

application = create_application()
