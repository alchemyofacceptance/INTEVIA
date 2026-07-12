"""Current governance status surface for INTEVIA v1.0 implementation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GovernanceStatus:
    """Report the current governed implementation context."""

    workstream: str
    status: str
    human_authority: str
    operating_frame: str


def current_status() -> GovernanceStatus:
    """Return the current governed INTEVIA implementation status."""
    return GovernanceStatus(
        workstream="INTEVIA v1.0 Implementation",
        status="implementation active",
        human_authority="Human Governor retains final authority",
        operating_frame="Run steadily. Observe consciously. Redesign only from recorded evidence.",
    )


def format_status(status: GovernanceStatus) -> str:
    """Return a Human-readable governance status summary."""
    return (
        f"Workstream: {status.workstream}\n"
        f"Status: {status.status}\n"
        f"Authority: {status.human_authority}\n"
        f"Operating frame: {status.operating_frame}"
    )
