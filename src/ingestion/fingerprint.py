"""Source fingerprinting.

Checksums answer the platform's first lineage question: *what exact source
file produced this table?*
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.platform.storage import sha256_file


@dataclass(frozen=True)
class SourceFingerprint:
    """Checksum and file facts for one physical source file."""

    source_path: str
    source_checksum: str
    size_bytes: int
    modified_at: str
    algorithm: str = "sha256"

    def as_dict(self) -> dict[str, Any]:
        """Dictionary form for manifests and the ingestion log."""
        return asdict(self)


def fingerprint_file(path: Path, repo_root: Path | None = None) -> SourceFingerprint:
    """Fingerprint a physical file, preferring repo-relative paths."""
    if not path.exists():
        raise FileNotFoundError(f"source file not found: {path}")
    stat = path.stat()
    relative: str = str(path)
    if repo_root is not None and path.is_relative_to(repo_root):
        relative = str(path.relative_to(repo_root))
    return SourceFingerprint(
        source_path=relative,
        source_checksum=sha256_file(path),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(timespec="seconds"),
    )


def new_run_id(dataset_id: str) -> str:
    """Create a human-readable, unique ingestion run id."""
    from uuid import uuid4

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"ing_{dataset_id}_{stamp}_{uuid4().hex[:8]}"
