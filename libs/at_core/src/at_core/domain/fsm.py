"""Twin lifecycle finite state machine (Doc 08 section 8.3).

The transition table is data, not conditional logic, so it can be exhaustively
tested and rendered as a diagram. Invalid commands never raise -- they produce a
rejection reason that the caller turns into a ``twin.command.rejected`` event.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from at_core.domain.enums import CommandType, TwinStatus

#: (current status, command) -> resulting status.
#: A command absent from this table is invalid for that status.
TRANSITIONS: Final[MappingProxyType[tuple[TwinStatus, CommandType], TwinStatus]] = MappingProxyType(
    {
        (TwinStatus.IDLE, CommandType.START): TwinStatus.RUNNING,
        (TwinStatus.RUNNING, CommandType.PAUSE): TwinStatus.PAUSED,
        (TwinStatus.PAUSED, CommandType.RESUME): TwinStatus.RUNNING,
        (TwinStatus.RUNNING, CommandType.PERFORM_MAINTENANCE): TwinStatus.MAINTENANCE,
        (TwinStatus.PAUSED, CommandType.PERFORM_MAINTENANCE): TwinStatus.MAINTENANCE,
        (TwinStatus.MAINTENANCE, CommandType.RESUME): TwinStatus.RUNNING,
        (TwinStatus.PAUSED, CommandType.RESET): TwinStatus.IDLE,
        (TwinStatus.FAILED, CommandType.RESET): TwinStatus.IDLE,
        (TwinStatus.FAILED, CommandType.RETIRE): TwinStatus.RETIRED,
        (TwinStatus.RUNNING, CommandType.RETIRE): TwinStatus.RETIRED,
        # Self-transitions: legal while running, do not change status.
        (TwinStatus.RUNNING, CommandType.SEEK): TwinStatus.RUNNING,
        (TwinStatus.PAUSED, CommandType.SEEK): TwinStatus.PAUSED,
        (TwinStatus.RUNNING, CommandType.SET_SPEED): TwinStatus.RUNNING,
        (TwinStatus.PAUSED, CommandType.SET_SPEED): TwinStatus.PAUSED,
        (TwinStatus.RUNNING, CommandType.INJECT_FAULT): TwinStatus.RUNNING,
        (TwinStatus.RUNNING, CommandType.SIMULATE): TwinStatus.RUNNING,
        (TwinStatus.PAUSED, CommandType.SIMULATE): TwinStatus.PAUSED,
    }
)

#: Statuses from which the replay clock advances.
ACTIVE_STATUSES: Final[frozenset[TwinStatus]] = frozenset({TwinStatus.RUNNING})

#: Terminal statuses that require an explicit RESET or RETIRE to leave.
TERMINAL_STATUSES: Final[frozenset[TwinStatus]] = frozenset({TwinStatus.FAILED, TwinStatus.RETIRED})


def can_apply(status: TwinStatus, command: CommandType) -> bool:
    """Whether ``command`` is legal from ``status``."""
    return (status, command) in TRANSITIONS


def next_status(status: TwinStatus, command: CommandType) -> TwinStatus | None:
    """Resulting status, or None when the transition is illegal."""
    return TRANSITIONS.get((status, command))


def rejection_reason(status: TwinStatus, command: CommandType) -> str:
    """Human-readable explanation for an illegal transition.

    Surfaced in the ``twin.command.rejected`` event payload and, via the API, in
    the ``TWIN_INVALID_TRANSITION`` problem detail (Doc 12 section 12.9).
    """
    if status in TERMINAL_STATUSES:
        return (
            f"Engine is {status.value}; only "
            f"{'RESET or RETIRE' if status is TwinStatus.FAILED else 'no commands'} "
            f"are accepted. Command {command.value} was ignored."
        )
    legal = sorted(cmd.value for (st, cmd) in TRANSITIONS if st is status)
    return (
        f"Command {command.value} is not valid while the twin is {status.value}. "
        f"Legal commands: {', '.join(legal) or 'none'}."
    )
