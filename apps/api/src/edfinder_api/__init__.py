"""Package namespace for the ED-Finder API.

The API source tree still contains legacy flat imports such as
`from config import settings`. Expose the parent `src/` directory on this
package's search path so runtime entrypoints can move to `edfinder_api.*`
without forcing a giant import rewrite in the same change.
"""

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_LEGACY_SRC_DIR = _PACKAGE_DIR.parent

__path__ = [str(_PACKAGE_DIR), str(_LEGACY_SRC_DIR)]
