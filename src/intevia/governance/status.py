"""Governance status surface for INTEVIA Sprint 1."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GovernanceStatus:
    """Report the current governed sprint context."""

    sprint: str
    status: str
    human_authority: str
    operating_frame: str


def current_status() -> GovernanceStatus:
    """Return the current governed status for the first sprint surface."""
    return GovernanceStatus(
        sprint="INTEVIA Sprint 1",
        status="governed build-study active",
        human_authority="Human Governor retains final authority",
        operating_frame="Build INTEVIA. Instrument INTEVIA.",
    )


def format_status(status: GovernanceStatus) -> str:
    """Return a Human-readable governance status summary."""
    return (
        f"Sprint: {status.sprint}\n"
        f"Status: {status.status}\n"
        f"Authority: {status.human_authority}\n"
        f"Operating frame: {status.operating_frame}"
    )
