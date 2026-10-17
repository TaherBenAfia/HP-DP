"""LanceDB-backed vector store for page embeddings.

The store is deliberately tiny: one table of ``(id, vector, metadata)`` rows
created lazily on the first write, so importing this module never touches the
filesystem until a caller actually persists embeddings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb


class LanceVectorStore:
    """Vector index over page embeddings stored in a local LanceDB database."""

    def __init__(self, db_path: Path, table_name: str = "page_embeddings") -> None:
        self.db_path = Path(db_path)
        self.table_name = table_name
        self._db = lancedb.connect(str(self.db_path))
        self._table: Any | None = None

    def _existing_table(self) -> Any | None:
        """Return the table when it already exists on disk, else ``None``."""
        if self._table is not None:
            return self._table
        if self.table_name in self._db.table_names():
            self._table = self._db.open_table(self.table_name)
        return self._table

    def add(
        self,
        ids: list[str],
        vectors: list[list[float]],
        metadata: list[dict[str, Any]] | None = None,
    ) -> int:
        """Insert embeddings with optional per-row metadata; return row count."""
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must have the same length")
        if metadata is not None and len(metadata) != len(ids):
            raise ValueError("metadata must align with ids")
        records: list[dict[str, Any]] = []
        for index, (row_id, vector) in enumerate(zip(ids, vectors, strict=True)):
            record: dict[str, Any] = {"id": row_id, "vector": [float(value) for value in vector]}
            if metadata is not None:
                record["metadata"] = dict(metadata[index] or {})
            records.append(record)
        table = self._existing_table()
        if table is None:
            self._table = self._db.create_table(self.table_name, records, mode="create")
        else:
            table.add(records)
        return len(records)

    def search(self, vector: list[float], k: int = 5) -> list[dict[str, Any]]:
        """Return the ``k`` nearest embeddings, closest first, with distances."""
        table = self._existing_table()
        if table is None:
            return []
        query = [float(value) for value in vector]
        return table.search(query).limit(k).to_list()

    def count(self) -> int:
        """Number of embeddings stored in the table (0 when it does not exist)."""
        table = self._existing_table()
        if table is None:
            return 0
        return int(table.count_rows())
