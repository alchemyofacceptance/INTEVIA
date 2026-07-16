from __future__ import annotations


def inspect_surfaces() -> str:
    """Return a static, deterministic description of the INTEVIA inspection surface.

    The returned string has no trailing newline. It reports visible surfaces only and
    introduces no routing, mutation, persistence, or hidden-state behaviour.
    """
    return (
        "INTEVIA inspection surface\n"
        "\n"
        "Command surfaces:\n"
        "- breathe\n"
        "- heartbeat\n"
        "- inspect\n"
        "- demo_activity_review\n"
        "- observation\n"
        "\n"
        "Governance surfaces:\n"
        "- current_status\n"
        "- format_status\n"
        "\n"
        "Observation surfaces:\n"
        "- ObservationJournal\n"
        "- ContributionService observation emission\n"
        "- Visible Witness snapshot rendering\n"
        "\n"
        "Evidence artefacts:\n"
        "- docs/evidence/sprints/sprint-1/SPRINT_1_EVIDENCE_LOG.md\n"
        "- docs/evidence/sprints/sprint-1/SPRINT_1_CONSTITUTIONAL_CHECKPOINT.md\n"
        "- docs/evidence/sprints/sprint-1/SPRINT_1_OPC_BOUNDED_DOCTRINE_NOTE.md\n"
        "- docs/evidence/sprints/sprint-1/WORK_BLOCK_7_BOUNDARY_CHARTER.md\n"
        "- docs/evidence/sprints/sprint-1/COE_Unit6_VisibleWitness_Implementation_Package_v0.3.md\n"
        "\n"
        "Boundary:\n"
        "Reports visible surfaces only. No routing, mutation, persistence, or hidden state."
    )


if __name__ == "__main__":
    print(inspect_surfaces())
