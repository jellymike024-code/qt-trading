import os
import tempfile
from pathlib import Path

# Keep all test artifacts inside the authorized workspace on Windows.
_temp = Path(__file__).resolve().parent.parent / ".tmp"
_temp.mkdir(exist_ok=True)
os.environ["TEMP"] = os.environ["TMP"] = str(_temp)
tempfile.tempdir = str(_temp)
