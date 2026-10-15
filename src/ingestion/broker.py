"""Event broker abstraction and in-memory broker implementation.

Provides decoupled topic publishing, consumer-group offsets, and commit checkpoints.
Enables local testing without requiring external Docker services while strictly
preserving partition-offset and consumer-group semantics.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any


class EventBroker(ABC):
    """Abstract interface for event streaming brokers."""

    @abstractmethod
    def publish(self, topic: str, event: dict[str, Any]) -> int:
        """Publish an event to a topic. Returns the assigned broker offset."""
        pass

    @abstractmethod
    def consume(
        self,
        topic: str,
        consumer_id: str,
        limit: int = 100,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Fetch uncommitted messages for a consumer group.
        
        Returns a list of (offset, event) tuples.
        """
        pass

    @abstractmethod
    def commit(self, consumer_id: str, topic: str, offset: int) -> None:
        """Commit consumer offset progress."""
        pass


class InMemoryBroker(EventBroker):
    """Deterministic, thread-safe in-memory message broker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # topic -> list of (offset, message)
        self._topics: dict[str, list[dict[str, Any]]] = {}
        # (consumer_id, topic) -> committed_offset
        self._committed_offsets: dict[tuple[str, str], int] = {}

    def publish(self, topic: str, event: dict[str, Any]) -> int:
        with self._lock:
            if topic not in self._topics:
                self._topics[topic] = []
            offset = len(self._topics[topic])
            self._topics[topic].append(event)
            return offset

    def publish_batch(self, topic: str, events: list[dict[str, Any]]) -> list[int]:
        with self._lock:
            if topic not in self._topics:
                self._topics[topic] = []
            offsets = []
            for event in events:
                offset = len(self._topics[topic])
                self._topics[topic].append(event)
                offsets.append(offset)
            return offsets

    def consume(
        self,
        topic: str,
        consumer_id: str,
        limit: int = 100,
    ) -> list[tuple[int, dict[str, Any]]]:
        with self._lock:
            if topic not in self._topics:
                return []
            current_offset = self._committed_offsets.get((consumer_id, topic), 0)
            messages = self._topics[topic][current_offset : current_offset + limit]
            return [(current_offset + i, msg) for i, msg in enumerate(messages)]

    def commit(self, consumer_id: str, topic: str, offset: int) -> None:
        with self._lock:
            self._committed_offsets[(consumer_id, topic)] = offset

    def get_committed_offset(self, consumer_id: str, topic: str) -> int:
        with self._lock:
            return self._committed_offsets.get((consumer_id, topic), 0)

    def total_messages(self, topic: str) -> int:
        with self._lock:
            return len(self._topics.get(topic, []))
