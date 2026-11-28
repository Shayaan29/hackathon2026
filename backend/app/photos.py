"""Camera snapshots of the parts: the cockpit's browser photographs each part while it rests under the sensor
and sends the picture here. One JPEG per part, named after the sensor and the part number, in backend/data/photos
(git-ignored). A new run deletes the pictures of that sensor, because the part numbers start again."""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("cockpit.photos")

MAX_BYTES = 3_000_000
KEEP = 3000
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class PhotoError(ValueError):
    pass


class PhotoStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def path(self, sid: str, number: int) -> Path:
        if not _SAFE.match(sid) or number < 1:
            raise PhotoError("unknown part")
        return self.folder / f"{sid}-{number}.jpg"

    def has(self, sid: str, number: int) -> bool:
        try:
            return self.path(sid, number).is_file()
        except PhotoError:
            return False

    def url(self, sid: str, number: int) -> str | None:
        return f"/photos/{sid}/{number}.jpg" if self.has(sid, number) else None

    def save(self, sid: str, number: int, data: bytes) -> str:
        if not data or len(data) > MAX_BYTES:
            raise PhotoError("The picture is empty or larger than 3 MB.")
        if not data.startswith(b"\xff\xd8"):
            raise PhotoError("The picture must be a JPEG.")
        target = self.path(sid, number)
        self.folder.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self._prune()
        return f"/photos/{sid}/{number}.jpg"

    def clear(self, sid: str) -> None:
        if not self.folder.is_dir() or not _SAFE.match(sid):
            return
        for item in self.folder.glob(f"{sid}-*.jpg"):
            try:
                item.unlink()
            except OSError as exc:
                log.warning("could not delete %s: %s", item, exc)

    def _prune(self) -> None:
        files = sorted(self.folder.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        for item in files[:-KEEP]:
            try:
                item.unlink()
            except OSError:
                pass
