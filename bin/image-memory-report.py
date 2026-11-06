#!/usr/bin/env python3
"""Summarize paired original and prepared image utility outcomes."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from image_memory_scoring import score_answer


VIEWS = ("original", "prepared")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: each row must be a JSON object")
            rows.append(row)
    return rows


def load_manifest(path: Path, split: str) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    samples: dict[str, dict[str, Any]] = {}
    groups: dict[str, str] = {}
    for line_number, row in enumerate(read_jsonl(path), 1):
        if row.get("split") != split:
            continue
        sample_id = row.get("sample_id")
        image_group = row.get("image_group")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"{path}:{line_number}: sample_id must be nonempty text")
        if not isinstance(image_group, str) or not image_group:
            raise ValueError(f"{path}:{line_number}: image_group must be nonempty text")
        if sample_id in samples:
            raise ValueError(f"{path}:{line_number}: duplicate sample_id {sample_id!r}")
        samples[sample_id] = row
        groups[sample_id] = image_group
    if not samples:
        raise ValueError(f"Manifest has no records in split {split!r}")
    return samples, groups


def bootstrap_interval(group_differences: dict[str, float], draws: int, seed: int) -> dict[str, Any] | None:
    values = list(group_differences.values())
    if not values:
        return None
    if draws < 1:
        raise ValueError("bootstrap draws must be positive")
    rng = random.Random(seed)
    estimates = [
        sum(rng.choice(values) for _ in values) / len(values)
        for _ in range(draws)
    ]
    estimates.sort()
    low_index = max(0, math.floor(0.025 * (draws - 1)))
    high_index = min(draws - 1, math.ceil(0.975 * (draws - 1)))
    return {
        "method": "percentile bootstrap over image groups",
        "confidence_level": 0.95,
        "draws": draws,
        "seed": seed,
        "lower": estimates[low_index],
        "upper": estimates[high_index],
    }


def build_report(manifest: Path, outcomes_path: Path, split: str,
                 draws: int, seed: int) -> dict[str, Any]:
    samples, sample_groups = load_manifest(manifest, split)
    outcomes: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    unmatched: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for line_number, row in enumerate(read_jsonl(outcomes_path), 1):
        sample_id = row.get("sample_id")
        view = row.get("view")
        if sample_id not in samples or row.get("split") != split or view not in VIEWS:
            unmatched.append({"line": line_number, "sample_id": sample_id, "view": view})
            continue
        if view in outcomes[sample_id]:
            duplicates.append({"line": line_number, "sample_id": sample_id, "view": view})
            continue
        scored = dict(row)
        extracted, correct = score_answer(row.get("prediction"), samples[sample_id])
        scored["extracted_answer"] = extracted
        scored["correct"] = correct
        outcomes[sample_id][view] = scored

    assigned = len(samples)
    view_summary: dict[str, dict[str, Any]] = {}
    for view in VIEWS:
        complete_ids = [
            sample_id for sample_id in samples
            if view in outcomes[sample_id]
            and outcomes[sample_id][view].get("error") is None
            and isinstance(outcomes[sample_id][view].get("prediction"), str)
            and bool(outcomes[sample_id][view]["prediction"].strip())
            and isinstance(outcomes[sample_id][view].get("correct"), bool)
        ]
        correct = sum(outcomes[sample_id][view]["correct"] for sample_id in complete_ids)
        assigned_group_scores: dict[str, list[float]] = defaultdict(list)
        completed_group_scores: dict[str, list[float]] = defaultdict(list)
        for sample_id in samples:
            group = sample_groups[sample_id]
            row = outcomes[sample_id].get(view)
            has_prediction = bool(
                row
                and row.get("error") is None
                and isinstance(row.get("prediction"), str)
                and row["prediction"].strip()
            )
            assigned_group_scores[group].append(
                float(bool(has_prediction and row.get("correct") is True))
            )
            if has_prediction and isinstance(row.get("correct"), bool):
                completed_group_scores[group].append(float(row["correct"]))
        assigned_group_means = [sum(values) / len(values) for values in assigned_group_scores.values()]
        completed_group_means = [sum(values) / len(values) for values in completed_group_scores.values()]
        view_summary[view] = {
            "assigned_questions": assigned,
            "completed_questions": len(complete_ids),
            "incomplete_questions": assigned - len(complete_ids),
            "correct_completed": correct,
            "accuracy_assigned_denominator": (
                sum(assigned_group_means) / len(assigned_group_means) if assigned_group_means else None
            ),
            "accuracy_completed_denominator": (
                sum(completed_group_means) / len(completed_group_means) if completed_group_means else None
            ),
            "accuracy_weighting": "equal-weight mean of per-image accuracy",
        }

    assigned_group_values: dict[str, list[float]] = defaultdict(list)
    completed_group_values: dict[str, list[float]] = defaultdict(list)
    completed_paired_questions = 0
    for sample_id in samples:
        pair = outcomes[sample_id]
        scores: dict[str, float] = {}
        complete = True
        for view in VIEWS:
            row = pair.get(view)
            has_prediction = bool(
                row
                and row.get("error") is None
                and isinstance(row.get("prediction"), str)
                and row["prediction"].strip()
            )
            scores[view] = float(bool(has_prediction and row.get("correct") is True))
            complete = complete and has_prediction
        difference = scores["prepared"] - scores["original"]
        group = sample_groups[sample_id]
        assigned_group_values[group].append(difference)
        if complete:
            completed_group_values[group].append(difference)
            completed_paired_questions += 1
    assigned_group_differences = {
        group: sum(differences) / len(differences)
        for group, differences in assigned_group_values.items()
    }
    completed_group_differences = {
        group: sum(differences) / len(differences)
        for group, differences in completed_group_values.items()
    }
    assigned_paired_difference = (
        sum(assigned_group_differences.values()) / len(assigned_group_differences)
        if assigned_group_differences else None
    )
    completed_paired_difference = (
        sum(completed_group_differences.values()) / len(completed_group_differences)
        if completed_group_differences else None
    )
    return {
        "manifest": str(manifest.resolve()),
        "outcomes": str(outcomes_path.resolve()),
        "split": split,
        "scoring_recomputed_from_final_text": True,
        "assigned_questions": assigned,
        "assigned_image_groups": len({sample_groups[sample_id] for sample_id in samples}),
        "views": view_summary,
        "paired_comparison": {
            "prepared_minus_original_accuracy": assigned_paired_difference,
            "paired_questions_assigned": assigned,
            "paired_image_groups_assigned": len(assigned_group_differences),
            "estimate_denominator": "all assigned questions, equal-weight mean across image groups",
            "bootstrap_confidence_interval": bootstrap_interval(assigned_group_differences, draws, seed),
        },
        "completed_pair_comparison": {
            "prepared_minus_original_accuracy": completed_paired_difference,
            "paired_questions_completed": completed_paired_questions,
            "paired_image_groups_completed": len(completed_group_differences),
            "estimate_denominator": "complete paired questions, equal-weight mean across image groups",
            "bootstrap_confidence_interval": bootstrap_interval(completed_group_differences, draws, seed),
        },
        "unmatched_output_rows": unmatched,
        "duplicate_output_rows": duplicates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("outcomes_jsonl", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--split", default="validation",
                        choices=("development", "validation", "held_out"))
    parser.add_argument("--bootstrap-draws", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=564)
    args = parser.parse_args()
    if args.bootstrap_draws < 1:
        parser.error("bootstrap draws must be positive")
    try:
        report = build_report(args.manifest, args.outcomes_jsonl, args.split,
                              args.bootstrap_draws, args.seed)
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                    encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
