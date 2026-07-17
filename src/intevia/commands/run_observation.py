from __future__ import annotations

from src.intevia.app import breathe
from src.intevia.governance.status import current_status, format_status


def run_observation() -> str:
    """Return the governed run-observation report without a trailing newline."""
    first_breath = breathe()
    formatted_status = format_status(current_status())

    return (
        "First Breath:\n"
        f"{first_breath}\n"
        "\n"
        "Governance Status:\n"
        f"{formatted_status}"
    )


def main() -> None:
    """Emit the governed run-observation report with one trailing newline."""
    print(run_observation())


if __name__ == "__main__":
    main()
