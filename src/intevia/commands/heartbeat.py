from src.intevia.app import breathe
from src.intevia.governance.status import current_status


def heartbeat() -> str:
    """Return the organism's first breath and governed sprint context."""
    breath = breathe()
    status = current_status()
    return (
        f"{breath}\n"
        f"Sprint: {status.sprint}\n"
        f"Status: {status.status}\n"
        f"Authority: {status.human_authority}\n"
        f"Operating frame: {status.operating_frame}"
    )


if __name__ == "__main__":
    print(heartbeat())
