"""Put the importer and API source folders on the import path for tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "apps" / "api" / "src", ROOT / "apps" / "importer" / "src"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
