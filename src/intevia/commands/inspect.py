def inspect_surfaces() -> str:
    """Return a static report of visible INTEVIA implementation surfaces."""
    report = [
        "INTEVIA inspection surface",
        "",
        "Command surfaces:",
        "- breathe",
        "- heartbeat",
        "",
        "Governance surfaces:",
        "- current_status",
        "- format_status",
        "",
        "Evidence artefacts:",
        "- docs/evidence/sprints/sprint-1/SPRINT_1_EVIDENCE_LOG.md",
        "- docs/evidence/sprints/sprint-1/SPRINT_1_CONSTITUTIONAL_CHECKPOINT.md",
        "- docs/evidence/sprints/sprint-1/SPRINT_1_OPC_BOUNDED_DOCTRINE_NOTE.md",
        "- docs/evidence/sprints/sprint-1/WORK_BLOCK_7_BOUNDARY_CHARTER.md",
        "",
        "Boundary:",
        "Reports visible surfaces only. No routing, mutation, persistence, or hidden state.",
    ]
    return "\n".join(report)


if __name__ == "__main__":
    print(inspect_surfaces())
