from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.intevia.review_surface import ReviewSurface


def _format_scalar(value: Any, field_name: str) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (str, int, float)):
        return str(value)

    type_name = type(value).__name__
    if field_name == "review_flags":
        raise TypeError(
            f"review_flags contains unsupported value type: {type_name}"
        )
    if field_name == "review_history":
        raise TypeError(
            f"review_history contains unsupported item type: {type_name}"
        )
    if field_name == "review_items":
        raise TypeError(
            f"review_items contains unsupported item type: {type_name}"
        )

    raise TypeError(f"{field_name} contains unsupported value type: {type_name}")


def _format_mapping(mapping: Mapping[Any, Any]) -> str:
    if not mapping:
        return "[]"

    lines: list[str] = []
    for key in sorted(mapping.keys(), key=str):
        value = mapping[key]
        lines.append(f"{str(key)}: {_format_scalar(value, 'review_flags')}")

    return "\n".join(lines)


def _format_sequence(sequence: Sequence[Any], field_name: str) -> str:
    if not sequence:
        return "[]"

    return "\n".join(_format_scalar(item, field_name) for item in sequence)


def review_snapshot(surface: ReviewSurface) -> str:
    review_window = (
        "None"
        if surface.review_window is None
        else surface.review_window.isoformat(timespec="seconds")
    )

    sections = [
        f"Review State:\n{surface.review_state.value}",
        f"Review Band:\n{surface.review_band.value}",
        f"Review Flags:\n{_format_mapping(surface.review_flags)}",
        f"Review Window:\n{review_window}",
        f"Review History:\n"
        f"{_format_sequence(surface.review_history, 'review_history')}",
        f"Review Items:\n"
        f"{_format_sequence(surface.review_items, 'review_items')}",
    ]

    return "\n\n".join(sections)
