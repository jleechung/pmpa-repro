"""Score final answers against the pinned MathVista labels."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


NUMBER = re.compile(
    r"^\s*\$?([-+]?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"\s*([a-zA-Z°%²³/^]+)?\s*$"
)


def extract_answer(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(
        r"(?:^|\n)\s*(?:the\s+)?(?:final\s+)?answer(?:\s+is)?\s*[:\-]?\s*(.+?)\s*$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = (match.group(1) if match else text).strip()
    return value.strip("`*$ ") or None


def normalize_text(value: Any) -> str:
    text = str(value).strip().casefold()
    text = re.sub(r"^[\(\[]?([a-z])(?:[\)\].:])\s*", r"\1 ", text)
    text = re.sub(r"[^\w.+-]+", " ", text)
    return " ".join(text.split())


def numeric_value(value: Any) -> tuple[Decimal, str] | None:
    match = NUMBER.fullmatch(str(value))
    if not match or not match.group(1):
        return None
    try:
        return Decimal(match.group(1).replace(",", "")), (match.group(2) or "").casefold()
    except InvalidOperation:
        return None


def canonical_choice(value: str, choices: Any) -> str:
    normalized = normalize_text(value)
    if isinstance(choices, list):
        label = re.match(r"^([a-z])(?:\s|[.):]|$)", normalized)
        if label:
            index = ord(label.group(1)) - ord("a")
            if 0 <= index < len(choices):
                return normalize_text(choices[index])
        for choice in choices:
            if normalized == normalize_text(choice):
                return normalize_text(choice)
    return normalized


def score_answer(text: str | None, row: dict[str, Any]) -> tuple[str | None, bool]:
    answer = extract_answer(text)
    if answer is None:
        return None, False
    reference = row.get("reference_answer")
    answer_type = str(row.get("answer_type", "text")).casefold()
    if answer_type in {"integer", "float"}:
        predicted = numeric_value(answer)
        expected = numeric_value(reference)
        if predicted is None or expected is None:
            return answer, False
        precision = row.get("answer_precision")
        if answer_type == "float" and precision is not None:
            try:
                places_value = Decimal(str(precision))
                places = int(places_value)
                if places_value != places or places < 0:
                    return answer, False
                correct = round(float(predicted[0]), places) == round(float(expected[0]), places)
            except (InvalidOperation, ValueError, OverflowError):
                return answer, False
        else:
            correct = predicted[0] == expected[0]
        if expected[1] and predicted[1] and predicted[1] != expected[1]:
            correct = False
        return answer, correct
    if answer_type == "list":
        def parts(value: Any) -> list[str]:
            values = value if isinstance(value, list) else re.split(r"[,;|]", str(value).strip().strip("[]()"))
            return [normalize_text(item) for item in values if normalize_text(item)]
        return answer, parts(answer) == parts(reference)
    predicted = canonical_choice(answer, row.get("choices"))
    expected = canonical_choice(str(reference), row.get("choices"))
    return answer, predicted == expected
