"""ClickHouse OLAP sink tests (no server required)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

pytest.importorskip("clickhouse_connect")


def _config(tmp_path: Path, **olap_overrides: Any) -> Any:
    from src.platform.config import OlapConfig, PlatformConfig

    return PlatformConfig(repo_root=tmp_path, olap=OlapConfig(**olap_overrides))


class _FakeClient:
    """Records DDL commands and inserts without touching the network."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.inserts: list[dict[str, Any]] = []

    def command(self, cmd: str, *_args: Any, **_kwargs: Any) -> str:
        self.commands.append(cmd)
        return "ok"

    def insert_df(self, **kwargs: Any) -> None:
        self.inserts.append(kwargs)

    def query_arrow(self, sql: str, *_args: Any, **_kwargs: Any) -> Any:
        import pyarrow as pa

        return pa.table({"answer": [42]})


def test_is_enabled_default_and_toggled(tmp_path: Path) -> None:
    from src.olap.clickhouse import is_enabled

    assert is_enabled(_config(tmp_path)) is False
    assert is_enabled(_config(tmp_path, enabled=True, url="")) is False
    assert is_enabled(_config(tmp_path, url="http://localhost:8123")) is False
    assert is_enabled(_config(tmp_path, enabled=True, url="http://localhost:8123")) is True


@pytest.mark.parametrize(
    ("dtype", "expected"),
    [
        (pl.Int64, "Int64"),
        (pl.Float64, "Float64"),
        (pl.Boolean, "Bool"),
        (pl.String, "String"),
        (pl.Date, "Date"),
        (pl.Datetime("us"), "DateTime64"),
        (pl.List(pl.Int64), "String"),
    ],
)
def test_polars_to_clickhouse_type(dtype: pl.DataType, expected: str) -> None:
    from src.olap.clickhouse import polars_to_clickhouse_type

    assert polars_to_clickhouse_type(dtype) == expected


def test_create_client_raises_when_disabled(tmp_path: Path) -> None:
    from src.olap.clickhouse import create_client

    with pytest.raises(ValueError):
        create_client(_config(tmp_path))


def test_sync_table_with_fake_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.olap import clickhouse

    fake = _FakeClient()
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeClient:
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(clickhouse, "_client_factory", factory)

    config = _config(
        tmp_path,
        enabled=True,
        url="https://click.example:8443/statseo",
        username="analyst",
        password="secret",
        database="statseo",
        table_prefix="t_",
    )
    frame = pl.DataFrame(
        {
            "page_id": ["a", "b"],
            "score": [0.1, 0.2],
            "hits": [1, 2],
            "flag": [True, False],
        }
    )
    table_id = clickhouse.sync_table(config, "page_metrics", frame)
    assert table_id == "statseo.t_page_metrics"

    assert captured["host"] == "click.example"
    assert captured["port"] == 8443
    assert captured["secure"] is True
    assert captured["database"] == "statseo"
    assert captured["username"] == "analyst"

    drop_command = next(cmd for cmd in fake.commands if cmd.startswith("DROP"))
    assert '"statseo"."t_page_metrics"' in drop_command
    create_command = next(cmd for cmd in fake.commands if cmd.startswith("CREATE"))
    assert '"page_id" String' in create_command
    assert '"score" Float64' in create_command
    assert '"hits" Int64' in create_command
    assert '"flag" Bool' in create_command
    assert "ENGINE = MergeTree" in create_command

    assert fake.inserts[0]["table"] == "t_page_metrics"
    assert fake.inserts[0]["database"] == "statseo"
    assert fake.inserts[0]["column_names"] == ["page_id", "score", "hits", "flag"]
    assert fake.inserts[0]["df"].shape == (2, 4)


def test_sync_table_without_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.olap import clickhouse

    fake = _FakeClient()
    monkeypatch.setattr(clickhouse, "_client_factory", lambda **kwargs: fake)
    config = _config(tmp_path, enabled=True, url="http://localhost:8123")
    frame = pl.DataFrame({"page_id": ["a"], "score": [0.5]})
    clickhouse.sync_table(config, "page_metrics", frame, replace=False)
    assert not any(cmd.startswith("DROP") for cmd in fake.commands)
    assert any(cmd.startswith("CREATE TABLE IF NOT EXISTS") for cmd in fake.commands)


def test_query_with_fake_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.olap import clickhouse

    monkeypatch.setattr(clickhouse, "_client_factory", lambda **kwargs: _FakeClient())
    config = _config(tmp_path, enabled=True, url="http://localhost:8123")
    result = clickhouse.query(config, "SELECT 42 AS answer")
    assert result.to_dict(as_series=False) == {"answer": [42]}


@pytest.mark.skipif(not os.environ.get("CLICKHOUSE_URL"), reason="no ClickHouse server")
def test_integration_roundtrip(tmp_path: Path) -> None:
    from src.olap import clickhouse

    config = _config(tmp_path, enabled=True, url=os.environ["CLICKHOUSE_URL"])
    frame = pl.DataFrame({"page_id": ["z"], "score": [0.9]})
    clickhouse.sync_table(config, "it_page_metrics", frame)
    result = clickhouse.query(config, "SELECT count(*) AS n FROM it_page_metrics")
    assert result["n"][0] == 1
