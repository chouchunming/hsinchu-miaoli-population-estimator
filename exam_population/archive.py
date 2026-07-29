from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile

from .models import ArtifactMetadata, StoredArtifact


REGION_SLUG = {
    "新竹縣": "hsinchu_county",
    "新竹市": "hsinchu_city",
    "苗栗縣": "miaoli_county",
}


class Archive:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root).resolve()

    def store(self, metadata: ArtifactMetadata, data: bytes) -> StoredArtifact:
        digest = hashlib.sha256(data).hexdigest()
        extension = Path(metadata.original_filename).suffix.lower()
        if not extension or len(extension) > 10:
            extension = ".bin"
        relative = (
            Path("raw")
            / REGION_SLUG[metadata.region]
            / metadata.dataset
            / f"{metadata.roc_year}{metadata.month:02d}"
            / f"{digest}{extension}"
        )
        destination = self.data_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise OSError(f"archive hash path 已存在但內容不符：{destination}")
            return StoredArtifact(metadata, destination, digest)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if destination.exists():
                temporary.unlink()
            else:
                temporary.replace(destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise OSError(f"archive 寫入後 SHA-256 不符：{destination}")
        finally:
            if temporary.exists():
                temporary.unlink()
        return StoredArtifact(metadata, destination, digest)
