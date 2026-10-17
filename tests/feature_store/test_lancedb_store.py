"""LanceDB vector store tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lancedb")


def test_add_search_count_and_metadata_roundtrip(tmp_path: Path) -> None:
    from src.feature_store.lancedb_store import LanceVectorStore

    db_path = tmp_path / "lancedb"
    store = LanceVectorStore(db_path)
    assert store.count() == 0
    assert store.search([1.0, 0.0, 0.0]) == []

    added = store.add(
        ids=["a", "b", "c"],
        vectors=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.9, 0.1, 0.0]],
        metadata=[
            {"score": 0.9, "tag": "x"},
            {"score": 0.1, "tag": "y"},
            {"score": 0.5, "tag": "z"},
        ],
    )
    assert added == 3
    assert store.count() == 3

    results = store.search([1.0, 0.0, 0.0], k=3)
    assert [row["id"] for row in results] == ["a", "c", "b"]
    distances = [row["_distance"] for row in results]
    assert distances == sorted(distances)
    assert results[0]["metadata"]["score"] == pytest.approx(0.9)
    assert results[0]["metadata"]["tag"] == "x"
    assert results[2]["metadata"]["tag"] == "y"

    reopened = LanceVectorStore(db_path)
    assert reopened.count() == 3


def test_add_without_metadata_and_length_validation(tmp_path: Path) -> None:
    from src.feature_store.lancedb_store import LanceVectorStore

    store = LanceVectorStore(tmp_path / "lancedb")
    with pytest.raises(ValueError):
        store.add(["only-id"], [[1.0, 2.0], [3.0, 4.0]])
    with pytest.raises(ValueError):
        store.add(["a", "b"], [[1.0, 2.0]], metadata=[{"x": 1}])

    assert store.add(["a"], [[1.0, 2.0]]) == 1
    results = store.search([1.0, 2.0], k=1)
    assert results[0]["id"] == "a"
    assert "metadata" not in results[0]
