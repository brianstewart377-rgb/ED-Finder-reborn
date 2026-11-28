"""Download ED-Finder's EDMC plugin (``GET /api/v1/plugin/download``).

"Haul — design", step 3. Members download the plugin from the Account page
and unzip it into EDMC's plugins folder; it is not listed in EDMC's plugin
browser. The zip is made from the plugin's own folder, which the API image
carries (apps/api/Dockerfile.release), so the plugin a member downloads is
always the one the running site was built with.
"""
from __future__ import annotations

import io
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/api/v1/plugin", tags=["plugin"])

FOLDER = "EDFinder"
FILES = ("load.py", "LICENSE", "README.md")
_HERE = Path(__file__).resolve()
# In the API image the plugin is beside the API's code (/app/edmc_plugin); in
# the repository it is in apps/edmc-plugin, four folders up from this file.
PLACES = tuple(
    [_HERE.parents[1] / "edmc_plugin" / FOLDER]
    + ([_HERE.parents[4] / "apps" / "edmc-plugin" / FOLDER] if len(_HERE.parents) > 4 else []))
_VERSION = re.compile(r'^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.MULTILINE)


def plugin_folder() -> Optional[Path]:
    for place in PLACES:
        if all((place / name).is_file() for name in FILES):
            return place
    return None


@lru_cache(maxsize=1)
def plugin_zip() -> tuple[str, bytes]:
    folder = plugin_folder()
    if folder is None:
        raise FileNotFoundError("the plugin's folder is not here")
    found = _VERSION.search((folder / "load.py").read_text(encoding="utf-8"))
    if found is None:
        raise FileNotFoundError("the plugin has no version")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            info = zipfile.ZipInfo("%s/%s" % (FOLDER, name), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (folder / name).read_bytes())
    return found.group(1), buffer.getvalue()


@router.get("/download", operation_id="downloadPlugin",
            responses={200: {"content": {"application/zip": {}}}})
async def download_plugin() -> Response:
    try:
        version, data = plugin_zip()
    except FileNotFoundError:
        raise HTTPException(404, "The plugin is not on this site") from None
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="EDFinder-plugin-%s.zip"' % version,
                 "Cache-Control": "no-cache"})
