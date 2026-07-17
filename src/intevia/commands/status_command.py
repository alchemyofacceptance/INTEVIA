from __future__ import annotations

from src.intevia.governance.status import current_status, format_status


def status_command() -> str:
    """Return the formatted governance status with no trailing newline."""
    return format_status(current_status())


def main() -> None:
    """Emit the formatted governance status with exactly one trailing newline."""
    print(status_command())


if __name__ == "__main__":
    main()
