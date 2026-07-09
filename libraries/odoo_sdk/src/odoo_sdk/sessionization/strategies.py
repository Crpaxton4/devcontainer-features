"""Strategy pattern for interchangeable sessionization algorithms.

Each :class:`SessionStrategyConfig` row maps a set of event types to a concrete
strategy. ``WindowedSessionStrategy`` merges timestamps into gap-separated
sessions; ``FixedDurationStrategy`` emits one fixed-length entry per event.
``SessionizationContext`` groups heterogeneous events by strategy and builds the
resulting :class:`TimeEntry` rows.

This module is pure: the ``config`` argument is typed only for hints and no I/O
is performed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .models import EventType, RawEvent, SessionStrategyConfig, TimeEntry
from .windows import compute_windows

if TYPE_CHECKING:  # pragma: no cover - hints only, avoids a config import cycle
    from .config import SessionizationConfig


def _entry_branch(events: list[RawEvent]) -> str:
    """Return a compact, de-duplicated branch context string."""
    return ", ".join(sorted({event.branch for event in events if event.branch}))


def _events_in_window(
    events: list[RawEvent], start: datetime, end: datetime
) -> list[RawEvent]:
    """Return source events whose timestamp falls inside ``[start, end]``."""
    return [event for event in events if start <= event.timestamp <= end]


def _fixed_entry_end(event: RawEvent, settings: SessionStrategyConfig) -> datetime:
    """Return the end timestamp for one fixed-duration strategy entry."""
    return event.timestamp + timedelta(seconds=settings.fixed_secs)


class SessionizationStrategy(ABC):
    """Interface for an interchangeable sessionization algorithm."""

    def __init__(self, settings: SessionStrategyConfig) -> None:
        self.settings = settings

    @abstractmethod
    def build_entries(
        self,
        group: "StrategyEventGroup",
        config: "SessionizationConfig",
        gap_secs: int | None = None,
    ) -> list[TimeEntry]:
        """Build :class:`TimeEntry` rows for one strategy group."""


@dataclass(frozen=True)
class StrategyEventGroup:
    """Events grouped for one strategy invocation."""

    strategy: SessionizationStrategy
    key: tuple
    events: list[RawEvent]


def _strategy_key_value(
    key: tuple, settings: SessionStrategyConfig, name: str
) -> str:
    """Return a named value from a configured strategy group key."""
    return str(key[settings.group_keys.index(name)])


def _strategy_time_entry(
    group: StrategyEventGroup,
    source_events: list[RawEvent],
    start: datetime,
    end: datetime,
) -> TimeEntry:
    """Build one :class:`TimeEntry` from a strategy result."""
    settings = group.strategy.settings
    repo = _strategy_key_value(group.key, settings, "repo")
    task_id = _strategy_key_value(group.key, settings, "task_id")
    return TimeEntry(
        task_id=task_id,
        repo=repo,
        pr_num=source_events[0].pr_num if source_events else 0,
        start=start,
        end=end,
        label=repo,
        branch=_entry_branch(source_events),
        source_events=source_events,
        strategy_name=settings.name,
        strategy_category=settings.category,
        activity_type=source_events[0].event_type.name if source_events else "",
    )


class WindowedSessionStrategy(SessionizationStrategy):
    """Sessionize grouped timestamps with a configurable inactivity gap."""

    def build_entries(
        self,
        group: StrategyEventGroup,
        config: "SessionizationConfig",
        gap_secs: int | None = None,
    ) -> list[TimeEntry]:
        gap = self.settings.gap_secs if gap_secs is None else gap_secs
        entries: list[TimeEntry] = []
        timestamps = [event.timestamp for event in group.events]
        for start, end in compute_windows(timestamps, gap, config):
            source_events = _events_in_window(group.events, start, end)
            entries.append(_strategy_time_entry(group, source_events, start, end))
        return entries


class FixedDurationStrategy(SessionizationStrategy):
    """Create one fixed-duration entry for each source event."""

    def build_entries(
        self,
        group: StrategyEventGroup,
        config: "SessionizationConfig",
        gap_secs: int | None = None,
    ) -> list[TimeEntry]:
        _ = config, gap_secs
        return [
            _strategy_time_entry(
                group,
                [event],
                event.timestamp,
                _fixed_entry_end(event, self.settings),
            )
            for event in sorted(group.events, key=lambda event: event.timestamp)
        ]


_STRATEGY_CLASSES: dict[str, type[SessionizationStrategy]] = {
    "session": WindowedSessionStrategy,
    "fixed": FixedDurationStrategy,
}


DEFAULT_SESSION_STRATEGY_CONFIGS: tuple[SessionStrategyConfig, ...] = (
    SessionStrategyConfig(
        "development",
        "Development",
        (EventType.COMMIT, EventType.AGENT),
        "session",
        ("strategy", "repo", "task_id"),
        sweep_enabled=True,
        fallback_action="advanced project implementation",
    ),
    SessionStrategyConfig(
        "merge",
        "Merge",
        (EventType.MERGE,),
        "fixed",
        ("strategy", "repo", "task_id", "event_type", "pr_num", "timestamp"),
        fallback_action="completed release update",
    ),
    SessionStrategyConfig(
        "review",
        "Review",
        (EventType.REVIEW,),
        "fixed",
        ("strategy", "repo", "task_id", "event_type", "pr_num", "timestamp"),
        fallback_action="validated project changes",
    ),
)


class DuplicateStrategyOwnershipError(ValueError):
    """Raised when one :class:`EventType` is claimed by more than one strategy.

    Single-strategy ownership is an invariant of sessionization: every event type
    must route to exactly one strategy, or an event could be counted under two
    sessions (double-counted time). This error names the offending event type and
    the competing strategies so misconfiguration is diagnosable.
    """


def validate_single_strategy_ownership(
    settings_rows: tuple[SessionStrategyConfig, ...],
) -> dict[EventType, str]:
    """Assert each event type is owned by exactly one strategy config row.

    This surfaces, over the same ownership map :func:`_strategy_event_type_map`
    builds, the single-strategy-ownership invariant as an explicit, callable
    check operating on flat config rows (no strategy instances required).

    :param settings_rows: The configured strategy rows to validate.
    :type settings_rows: tuple[SessionStrategyConfig, ...]
    :raises DuplicateStrategyOwnershipError: When any event type is owned by more
        than one row.
    :return: Mapping of each covered event type to its owning strategy name.
    :rtype: dict[EventType, str]
    """
    owner: dict[EventType, str] = {}
    for row in settings_rows:
        for event_type in row.event_types:
            existing = owner.get(event_type)
            if existing is not None and existing != row.name:
                raise DuplicateStrategyOwnershipError(
                    f"event type {event_type.name} is owned by multiple "
                    f"strategies: {existing!r} and {row.name!r}"
                )
            owner[event_type] = row.name
    return owner


def _strategy_event_type_map(
    strategies: list[SessionizationStrategy],
) -> dict[EventType, SessionizationStrategy]:
    """Return an event-type to strategy map, rejecting duplicate coverage."""
    by_type: dict[EventType, SessionizationStrategy] = {}
    for strategy in strategies:
        for event_type in strategy.settings.event_types:
            if event_type in by_type:
                raise DuplicateStrategyOwnershipError(
                    f"event type {event_type.name} is owned by multiple "
                    f"strategies: {by_type[event_type].settings.name!r} and "
                    f"{strategy.settings.name!r}"
                )
            by_type[event_type] = strategy
    _validate_strategy_coverage(by_type)
    return by_type


def _validate_strategy_coverage(
    by_type: dict[EventType, SessionizationStrategy],
) -> None:
    """Raise if any :class:`EventType` lacks a configured strategy."""
    missing = set(EventType) - set(by_type)
    if missing:
        names = ", ".join(sorted(event_type.name for event_type in missing))
        raise ValueError(f"missing strategy for {names}")


def _strategy_from_settings(
    settings: SessionStrategyConfig,
) -> SessionizationStrategy:
    """Instantiate the concrete strategy configured by one settings row."""
    strategy_class = _STRATEGY_CLASSES.get(settings.strategy_kind)
    if strategy_class is None:
        raise ValueError(f"unknown strategy kind {settings.strategy_kind}")
    return strategy_class(settings)


def _strategy_group_key(
    settings: SessionStrategyConfig, event: RawEvent, task_id: str
) -> tuple:
    """Return a data-driven grouping key for one event/task pair."""
    values = {
        "strategy": settings.name,
        "repo": event.repo,
        "task_id": task_id,
        "event_type": event.event_type.name,
        "pr_num": event.pr_num,
        "timestamp": event.timestamp.isoformat(),
    }
    return tuple(values[name] for name in settings.group_keys)


class SessionizationContext:
    """Context that delegates sessionization to strategy objects."""

    def __init__(self, strategies: list[SessionizationStrategy]) -> None:
        self.strategies = strategies
        self.by_event_type = _strategy_event_type_map(strategies)

    def groups(self, events: list[RawEvent]) -> list[StrategyEventGroup]:
        """Classify and group events for strategy execution."""
        groups: dict[tuple, list[RawEvent]] = {}
        strategy_by_key: dict[tuple, SessionizationStrategy] = {}
        for event in events:
            for task_id in event.task_ids or ["UNKNOWN"]:
                strategy = self.by_event_type[event.event_type]
                key = _strategy_group_key(strategy.settings, event, task_id)
                groups.setdefault(key, []).append(event)
                strategy_by_key[key] = strategy
        return [
            StrategyEventGroup(strategy_by_key[key], key, value)
            for key, value in groups.items()
        ]

    def build_entries(
        self,
        events: list[RawEvent],
        gap_secs: int | None,
        config: "SessionizationConfig",
    ) -> list[TimeEntry]:
        """Build sorted entries by delegating to configured strategies."""
        entries: list[TimeEntry] = []
        for group in self.groups(events):
            gap = gap_secs if group.strategy.settings.sweep_enabled else None
            entries.extend(group.strategy.build_entries(group, config, gap))
        return sorted(
            entries, key=lambda entry: (entry.start, entry.repo, entry.task_id)
        )


def make_sessionization_context(
    settings_rows: tuple[SessionStrategyConfig, ...] = DEFAULT_SESSION_STRATEGY_CONFIGS,
) -> SessionizationContext:
    """Build a strategy context from flat config rows, ordered by priority."""
    strategies = [
        _strategy_from_settings(settings)
        for settings in sorted(settings_rows, key=lambda row: row.priority)
    ]
    return SessionizationContext(strategies)
