"""OpenLineage lineage emission.

Builds an OpenLineage client backed by the file transport and emits
``RunEvent`` START/COMPLETE/FAIL records as JSON lines so that pipeline runs
carry end-to-end lineage metadata alongside their data products.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from src.platform.config import PlatformConfig

_PRODUCER = "https://github.com/anomalyco/statseo/observability"
_DATASET_NAMESPACE = "statseo_local"


def create_client(config: PlatformConfig) -> Any:
    """Build a file-transport ``OpenLineageClient``; ``None`` when disabled."""
    ol_config = config.observability.openlineage
    if not ol_config.enabled:
        return None
    if ol_config.transport != "file":
        return None

    from openlineage.client import OpenLineageClient
    from openlineage.client.transport.file import FileConfig, FileTransport

    file_path = config.resolve(ol_config.file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    transport_config = FileConfig(log_file_path=str(file_path), append=True)
    transport = FileTransport(transport_config)
    return OpenLineageClient(transport=transport)


def _now() -> str:
    """ISO-8601 UTC timestamp for OpenLineage events."""
    return datetime.now(UTC).isoformat()


def _normalize_run_id(run_id: str) -> str:
    """Return a valid UUID string, deriving a stable one from non-UUID ids."""
    try:
        return str(uuid.UUID(str(run_id)))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(run_id)))


class OpenLineageEmitter:
    """Emit OpenLineage run events for one job namespace."""

    def __init__(self, config: PlatformConfig) -> None:
        self.config = config
        self.namespace = config.observability.openlineage.namespace
        self.client = create_client(config)

    def _dataset(self, name: str) -> Any:
        from openlineage.client.run import InputDataset

        return InputDataset(namespace=_DATASET_NAMESPACE, name=name)

    def _build_event(
        self,
        state: Any,
        job_name: str,
        run_id: str,
        inputs: Sequence[str] | None,
        outputs: Sequence[str] | None,
    ) -> Any:
        from openlineage.client.run import (
            Job,
            OutputDataset,
            Run,
            RunEvent,
        )

        job = Job(namespace=self.namespace, name=job_name)
        run = Run(runId=_normalize_run_id(run_id))
        event = RunEvent(
            eventType=state,
            eventTime=_now(),
            run=run,
            job=job,
            producer=_PRODUCER,
            inputs=[self._dataset(name) for name in (inputs or [])],
            outputs=[
                OutputDataset(namespace=_DATASET_NAMESPACE, name=name)
                for name in (outputs or [])
            ],
        )
        return event

    def _emit(
        self,
        state: Any,
        job_name: str,
        run_id: str,
        inputs: Sequence[str] | None,
        outputs: Sequence[str] | None,
    ) -> None:
        if self.client is None:
            return
        event = self._build_event(state, job_name, run_id, inputs, outputs)
        self.client.emit(event)

    def emit_start(
        self,
        job_name: str,
        run_id: str,
        inputs: Sequence[str] | None = None,
        outputs: Sequence[str] | None = None,
    ) -> None:
        """Emit a START event for ``job_name``/``run_id``."""
        from openlineage.client.run import RunState

        self._emit(RunState.START, job_name, run_id, inputs, outputs)

    def emit_complete(
        self,
        job_name: str,
        run_id: str,
        inputs: Sequence[str] | None = None,
        outputs: Sequence[str] | None = None,
    ) -> None:
        """Emit a COMPLETE event for ``job_name``/``run_id``."""
        from openlineage.client.run import RunState

        self._emit(RunState.COMPLETE, job_name, run_id, inputs, outputs)

    def emit_fail(
        self,
        job_name: str,
        run_id: str,
        inputs: Sequence[str] | None = None,
        outputs: Sequence[str] | None = None,
    ) -> None:
        """Emit a FAIL event for ``job_name``/``run_id``."""
        from openlineage.client.run import RunState

        self._emit(RunState.FAIL, job_name, run_id, inputs, outputs)


def emit_pipeline_lineage(
    config: PlatformConfig, stages: list[tuple[str, str]]
) -> int:
    """Emit START+COMPLETE for each ``(job_name, output_name)`` stage.

    Returns the number of OpenLineage events emitted.
    """
    emitter = OpenLineageEmitter(config)
    if emitter.client is None:
        return 0
    count = 0
    for job_name, output_name in stages:
        run_id = str(uuid.uuid4())
        emitter.emit_start(job_name, run_id, outputs=[output_name])
        emitter.emit_complete(job_name, run_id, outputs=[output_name])
        count += 2
    return count
