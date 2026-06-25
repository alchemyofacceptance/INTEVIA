from src.intevia.app import breathe
from src.intevia.governance.status import current_status, format_status


if __name__ == "__main__":
    print(breathe())
    print(format_status(current_status()))
