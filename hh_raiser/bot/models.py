from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True)
class ManagedInstance:
    """One allow-listed HHRaiser service and its private state directory."""

    key: str
    name: str
    service_name: str
    state_dir: Path


@dataclass(frozen=True)
class BotSettings:
    """Validated settings required by the Telegram control process."""

    token: str
    allowed_user_ids: frozenset[int]
    instances: Mapping[str, ManagedInstance]
    log_lines: int = 25
    summary_interval_minutes: int = 0
    user_systemd: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "instances", MappingProxyType(dict(self.instances)))


@dataclass(frozen=True)
class ServiceSnapshot:
    """Small, presentation-neutral view of a systemd unit."""

    active_state: str
    sub_state: str
    main_pid: int | None
    description: str = ""

    @property
    def is_active(self) -> bool:
        return self.active_state == "active"


@dataclass(frozen=True)
class InstanceStatistics:
    """Aggregated non-sensitive counters read from an instance database."""

    generation: int
    discovered: int
    viewed_vacancies: int
    total_views: int
    evaluated: int
    average_match_score: float | None
    responses_by_status: Mapping[str, int]
    next_raise_at: datetime | None
