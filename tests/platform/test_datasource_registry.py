"""Datasource contract and registry tests."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from src.ingestion.contracts import FieldOrigin
from src.platform.config import load_platform_config
from src.platform.datasource_registry import DatasourceRegistry

DATASET = "sports_seo_tunisia_v1"


@pytest.fixture(scope="module")
def registry() -> DatasourceRegistry:
    return DatasourceRegistry(load_platform_config())


def test_dataset_registered(registry: DatasourceRegistry) -> None:
    assert DATASET in registry.ids()


def test_contract_attributes(registry: DatasourceRegistry) -> None:
    contract = registry.get(DATASET)
    assert contract.domain == "sports_seo"
    assert contract.primary_key == ["url", "query"]
    assert len(contract.attributes) == 256
    assert len(contract.source_attributes) == 213
    assert len(contract.platform_attributes) == 43


def test_contract_origins(registry: DatasourceRegistry) -> None:
    contract = registry.get(DATASET)
    origins = Counter(attribute.origin for attribute in contract.attributes)
    assert origins[FieldOrigin.DERIVED_FEATURE] > 0
    assert origins[FieldOrigin.RAW_SOURCE] == 78
    # Platform-produced attributes must never claim to exist in the source.
    for attribute in contract.platform_attributes:
        assert attribute.present_in_source is False


def test_source_file_exists(registry: DatasourceRegistry) -> None:
    contract = registry.get(DATASET)
    path = registry.resolve_path(contract)
    assert path.exists()
    assert path.stat().st_size > 1_000_000


def test_validation_passes(registry: DatasourceRegistry) -> None:
    report = registry.validate(DATASET)
    assert report.passed, [check.name for check in report.failures]
    names = {check.name for check in report.checks}
    assert "required_columns_present" in names
    assert "source_checksum" in names


def test_checksum_is_stable(registry: DatasourceRegistry) -> None:
    contract = registry.get(DATASET)
    assert registry.checksum(contract) == registry.checksum(contract)
    assert len(registry.checksum(contract)) == 64


def test_ingestion_log_records_a_run(registry: DatasourceRegistry) -> None:
    from src.ingestion.pipeline import ingest

    result = ingest(DATASET)  # idempotent: re-uses an unchanged source checksum
    assert result.status in {"success", "skipped"}
    last = registry.last_successful_run(DATASET)
    assert last is not None
    assert last["dataset_id"] == DATASET
    assert Path(str(last["source_path"])).name.endswith(".csv")
