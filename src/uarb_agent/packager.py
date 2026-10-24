import logging
import zipfile
from pathlib import Path

from pydantic import BaseModel

from .config import settings

logger = logging.getLogger(__name__)


class ZipResult(BaseModel):
    path: Path | None  # None when no files were included
    included: list[Path]
    skipped: list[Path]  # left out to stay under the attachment size limit


def make_zip(files: list[Path], dest: Path, max_bytes: int | None = None) -> ZipResult:
    """Zip files in order, skipping any that would push the archive past max_bytes.

    Uses uncompressed sizes as the budget: PDFs barely compress, so this is a safe upper bound.
    """
    max_bytes = max_bytes or settings.max_attachment_bytes
    included: list[Path] = []
    skipped: list[Path] = []
    total = 0
    for f in files:
        size = f.stat().st_size
        if total + size > max_bytes:
            skipped.append(f)
            continue
        included.append(f)
        total += size

    if not included:
        return ZipResult(path=None, included=[], skipped=skipped)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in included:
            zf.write(f, arcname=f.name)
    logger.info("Zipped %d files (%d skipped) into %s (%d bytes)", len(included), len(skipped), dest, dest.stat().st_size)
    return ZipResult(path=dest, included=included, skipped=skipped)
