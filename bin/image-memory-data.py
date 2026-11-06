#!/usr/bin/env python3
"""Validate image-question records, group duplicate images, and prepare canvases."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError as exc:  # pragma: no cover - user-facing dependency error.
    raise SystemExit("Pillow is required: install it in the experiment environment.") from exc


REQUIRED = ("source_id", "question", "reference_answer", "original_path")
DEFAULT_SPLITS = {"development": 128, "validation": 32, "held_out": 128}
MATHVISTA_REVISION = "f1cff051238ef9955e11987576014a59f2d28f60"
RESAMPLING_FILTERS = {
    "lanczos": Image.Resampling.LANCZOS,
    "bicubic": Image.Resampling.BICUBIC,
    "bilinear": Image.Resampling.BILINEAR,
}


@dataclass
class Record:
    row: dict[str, Any]
    image_path: Path
    sha256: str
    perceptual_hash: int
    width: int
    height: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def perceptual_hash(path: Path) -> int:
    """Return a 64-bit average hash for near-duplicate grouping."""
    with Image.open(path) as source:
        gray = source.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
    values = list(gray.getdata())
    mean = sum(values) / len(values)
    value = 0
    for pixel in values:
        value = (value << 1) | int(pixel >= mean)
    return value


def load_records(input_path: Path) -> list[Record]:
    records: list[Record] = []
    seen_ids: set[str] = set()
    with input_path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{input_path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{input_path}:{line_number}: each record must be a JSON object")
            missing = [key for key in REQUIRED if key not in row or row[key] in (None, "")]
            if missing:
                raise ValueError(f"{input_path}:{line_number}: missing fields: {', '.join(missing)}")
            if not isinstance(row["question"], str) or not isinstance(row["reference_answer"], (str, int, float, bool)):
                raise ValueError(f"{input_path}:{line_number}: question and answer must be text or scalar")
            source_id = str(row["source_id"])
            if source_id in seen_ids:
                raise ValueError(f"{input_path}:{line_number}: duplicate source_id {source_id!r}")
            seen_ids.add(source_id)
            image_path = Path(row["original_path"]).expanduser().resolve()
            if not image_path.is_file():
                raise ValueError(f"{input_path}:{line_number}: image does not exist: {image_path}")
            try:
                with Image.open(image_path) as image:
                    image.verify()
                with Image.open(image_path) as image:
                    width, height = image.size
            except Exception as exc:
                raise ValueError(f"{input_path}:{line_number}: cannot decode image {image_path}: {exc}") from exc
            records.append(Record(row, image_path, sha256_file(image_path), perceptual_hash(image_path), width, height))
    if not records:
        raise ValueError(f"No records found in {input_path}")
    return records


def field_value(row: dict[str, Any], field_path: str | None) -> Any:
    if not field_path:
        return None
    value: Any = row
    for part in field_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def save_dataset_image(value: Any, target: Path) -> None:
    """Save a decoded Hugging Face image feature without guessing its encoding."""
    target.parent.mkdir(parents=True, exist_ok=True)
    image_value = value
    if isinstance(value, dict):
        if value.get("bytes"):
            from io import BytesIO

            image_value = Image.open(BytesIO(value["bytes"]))
        elif value.get("path"):
            image_value = Image.open(value["path"])
        else:
            raise ValueError("embedded image object has neither bytes nor path")
    if not hasattr(image_value, "save"):
        raise ValueError(f"unsupported embedded image value: {type(value).__name__}")
    image_value.convert("RGB").save(target, format="PNG", optimize=True)


def load_mathvista(image_dir: Path, source_family_field: str, context_field: str,
                   source_family_filters: set[str], context_filters: set[str], language_filter: str,
                   exclusions_path: Path) -> tuple[Path, dict[str, Any]]:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise ValueError("MathVista loading requires Hugging Face datasets; install datasets or pass normalized JSONL.") from exc
    try:
        dataset = load_dataset("AI4Math/MathVista", revision=MATHVISTA_REVISION, split="testmini")
    except Exception as exc:
        raise ValueError(f"Could not load MathVista testmini at revision {MATHVISTA_REVISION}: {exc}") from exc

    exclusions: list[dict[str, Any]] = []
    exclusion_counts: dict[str, int] = {}
    excluded_languages: dict[str, int] = {}
    excluded_contexts: dict[str, int] = {}
    normalized_path = image_dir / "mathvista-normalized-input.jsonl"
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    with normalized_path.open("w", encoding="utf-8") as stream:
        for index, source in enumerate(dataset):
            row = dict(source)
            pid = row.get("pid")
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            family = field_value(row, source_family_field)
            context = field_value(row, context_field)
            language = metadata.get("language")
            reason = None
            if source_family_filters and str(family) not in source_family_filters:
                reason = "source_family_filter"
            elif language_filter != "any" and str(language).casefold() != language_filter.casefold():
                reason = "language_filter"
            elif context_filters and str(context) not in context_filters:
                reason = "context_filter"
            elif not isinstance(pid, (str, int)) or not isinstance(row.get("question"), str):
                reason = "missing_pid_or_question"
            elif row.get("answer") is None or str(row.get("answer")).strip() == "":
                reason = "missing_answer"
            if reason:
                exclusions.append({"pid": pid, "index": index, "reason": reason,
                                   "source_family": family, "context": context, "language": language})
                exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
                if reason == "language_filter":
                    key = str(language)
                    excluded_languages[key] = excluded_languages.get(key, 0) + 1
                if reason == "context_filter":
                    key = str(context)
                    excluded_contexts[key] = excluded_contexts.get(key, 0) + 1
                continue
            image_path = image_dir / f"{pid}.png"
            try:
                save_dataset_image(row.get("decoded_image", row.get("image")), image_path)
            except Exception as exc:
                exclusions.append({"pid": pid, "index": index, "reason": "image_extraction_error",
                                   "detail": str(exc), "source_family": family, "context": context,
                                   "language": language})
                exclusion_counts["image_extraction_error"] = exclusion_counts.get("image_extraction_error", 0) + 1
                continue
            normalized = {
                "dataset_revision": MATHVISTA_REVISION,
                "dataset_usage": "evaluation_only",
                "training_use_permitted": False,
                "source_id": str(pid),
                "source_family": family,
                "question": row["question"],
                "choices": row.get("choices"),
                "reference_answer": row["answer"],
                "answer_type": row.get("answer_type"),
                "answer_precision": row.get("precision"),
                "metadata": row.get("metadata"),
                "image_type": context,
                "language": language,
                "original_path": str(image_path.resolve()),
            }
            stream.write(json.dumps(normalized, ensure_ascii=False) + "\n")
    exclusions_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusions_path.open("w", encoding="utf-8") as stream:
        for item in exclusions:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    return normalized_path, {"reasons": exclusion_counts,
                             "languages": excluded_languages,
                             "contexts": excluded_contexts}


def image_groups(records: list[Record], max_hash_distance: int) -> list[list[int]]:
    parents = list(range(len(records)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = root(left), root(right)
        if a != b:
            parents[max(a, b)] = min(a, b)

    exact: dict[str, int] = {}
    for index, record in enumerate(records):
        if record.sha256 in exact:
            union(index, exact[record.sha256])
        else:
            exact[record.sha256] = index
    for left in range(len(records)):
        for right in range(left + 1, len(records)):
            distance = bin(records[left].perceptual_hash ^ records[right].perceptual_hash).count("1")
            if distance <= max_hash_distance:
                union(left, right)
    grouped: dict[int, list[int]] = {}
    for index in range(len(records)):
        grouped.setdefault(root(index), []).append(index)
    return sorted(grouped.values(), key=lambda group: min(records[i].sha256 for i in group))


def adjusted_targets(targets: dict[str, int], total: int) -> dict[str, int]:
    if sum(targets.values()) <= total:
        return dict(targets)
    scale = total / sum(targets.values())
    adjusted = {name: int(target * scale) for name, target in targets.items()}
    remainder = total - sum(adjusted.values())
    for name in targets:
        if remainder == 0:
            break
        adjusted[name] += 1
        remainder -= 1
    return adjusted


def assign_splits(groups: list[list[int]], targets: dict[str, int], seed: int) -> dict[int, str]:
    targets = adjusted_targets(targets, sum(len(group) for group in groups))
    names = [name for name, target in targets.items() if target > 0]
    order = list(range(len(groups)))
    random.Random(seed).shuffle(order)
    counts = {name: 0 for name in names}
    assignment: dict[int, str] = {}
    for group_index in order:
        size = len(groups[group_index])
        choices = [name for name in names if counts[name] + size <= targets[name]]
        if not choices:
            continue
        chosen = min(choices, key=lambda name: ((counts[name] + size) / targets[name], names.index(name)))
        assignment[group_index] = chosen
        counts[chosen] += size
    return assignment


def prepare_image(source: Path, target: Path, canvas: tuple[int, int],
                  resampling: str = "lanczos") -> dict[str, Any]:
    if resampling not in RESAMPLING_FILTERS:
        raise ValueError(f"unsupported resampling filter: {resampling}")
    resampling_filter = RESAMPLING_FILTERS[resampling]
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    source_size = image.size
    scale = min(canvas[0] / image.width, canvas[1] / image.height)
    resized_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    resized = image.resize(resized_size, resampling_filter)
    left = (canvas[0] - resized_size[0]) // 2
    top = (canvas[1] - resized_size[1]) // 2
    prepared = Image.new("RGB", canvas, (255, 255, 255))
    prepared.paste(resized, (left, top))
    target.parent.mkdir(parents=True, exist_ok=True)
    prepared.save(target, format="PNG", optimize=True)

    # Remove padding, then map the prepared pixels back to source dimensions.
    content = prepared.crop((left, top, left + resized_size[0], top + resized_size[1]))
    reconstructed = content.resize(source_size, resampling_filter)
    difference = ImageChops.difference(image, reconstructed)
    max_change = max(max(channel.getextrema()) for channel in difference.split())
    channel_rms = ImageStat.Stat(difference).rms
    rmse = (sum(value ** 2 for value in channel_rms) / len(channel_rms)) ** 0.5
    difference_path = target.with_name(f"{target.stem}.difference_x4.png")
    difference.point(lambda value: min(value * 4, 255)).save(difference_path, format="PNG", optimize=True)
    return {
        "source_dimensions": list(source_size),
        "prepared_dimensions": list(canvas),
        "resized_dimensions": list(resized_size),
        "padding": {"left": left, "top": top, "right": canvas[0] - resized_size[0] - left,
                    "bottom": canvas[1] - resized_size[1] - top},
        "scale": scale,
        "format": "PNG",
        "resampling": resampling.upper(),
        "padding_color_rgb": [255, 255, 255],
        "comparison": "prepared content cropped and resized back to source dimensions",
        "max_abs_channel_change_8bit": max_change,
        "rmse_8bit": rmse,
        "difference_image_path": str(difference_path.resolve()),
        "difference_image_scale": 4,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path,
                        help="Normalized mode: INPUT.jsonl OUTPUT.jsonl. MathVista mode: OUTPUT.jsonl.")
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--mathvista", action="store_true", help="Load the pinned MathVista testmini split.")
    parser.add_argument("--image-dir", type=Path, help="Directory for extracted MathVista source images.")
    parser.add_argument("--exclusions", type=Path, help="JSONL path for MathVista exclusions.")
    parser.add_argument("--source-family-field", default="metadata.source",
                        help="MathVista field for source family (default: metadata.source).")
    parser.add_argument("--source-family", action="append", default=[], help="Include this exact source-family value; repeat to allow several.")
    parser.add_argument("--context-field", default="metadata.context",
                        help="MathVista field for visual context (default: metadata.context).")
    parser.add_argument("--context", action="append", default=None,
                        help="Include this visual context; repeat to allow several. Defaults to charts and diagrams.")
    parser.add_argument("--language", default="English", help="Question language to include; use 'any' to include every language (default: English).")
    parser.add_argument("--canvas", type=int, nargs=2, default=(1024, 1024), metavar=("WIDTH", "HEIGHT"))
    parser.add_argument(
        "--resampling",
        action="append",
        choices=tuple(RESAMPLING_FILTERS),
        help=(
            "Prepare this resize-filter candidate. Repeat the option to prepare several "
            "candidates. The default is lanczos."
        ),
    )
    parser.add_argument("--seed", type=int, default=564)
    parser.add_argument("--development", type=int, default=128)
    parser.add_argument("--validation", type=int, default=32)
    parser.add_argument("--held-out", type=int, default=128)
    parser.add_argument("--max-hash-distance", type=int, default=4, help="Maximum 64-bit average-hash Hamming distance for near-duplicate grouping.")
    args = parser.parse_args()
    if args.mathvista:
        if len(args.paths) != 1 or not args.image_dir:
            parser.error("MathVista mode needs one output path and --image-dir")
        input_path = None
        output_path = args.paths[0]
    else:
        if len(args.paths) != 2:
            parser.error("normalized mode needs INPUT.jsonl and OUTPUT.jsonl")
        input_path, output_path = args.paths
    if min(args.canvas) < 32 or args.max_hash_distance < 0 or args.max_hash_distance > 64:
        parser.error("canvas dimensions must be at least 32; hash distance must be between 0 and 64")
    targets = {"development": args.development, "validation": args.validation, "held_out": args.held_out}
    if any(value < 0 for value in targets.values()) or sum(targets.values()) == 0:
        parser.error("split sizes must be nonnegative and their sum must be positive")
    resampling_candidates = list(dict.fromkeys(args.resampling or ("lanczos",)))
    try:
        exclusion_counts = {}
        if args.mathvista:
            exclusion_path = args.exclusions or output_path.with_name("exclusions.jsonl")
            input_path, exclusion_counts = load_mathvista(
                args.image_dir.resolve(), args.source_family_field, args.context_field,
                set(args.source_family),
                set(args.context or ("geometry diagram", "bar chart", "line plot", "scatter plot", "pie chart", "function plot")),
                args.language, exclusion_path.resolve(),
            )
        assert input_path is not None
        records = load_records(input_path)
        groups = image_groups(records, args.max_hash_distance)
        effective_targets = adjusted_targets(targets, len(records))
        group_assignment = assign_splits(groups, effective_targets, args.seed)
        split_exclusions = []
        sample_group: dict[int, str] = {}
        for group_index, members in enumerate(groups):
            group_hashes = sorted(records[index].sha256 for index in members)
            group_name = hashlib.sha256("\n".join(group_hashes).encode()).hexdigest()[:16]
            for index in members:
                sample_group[index] = group_name
        output_rows = []
        for group_index, members in enumerate(groups):
            split_name = group_assignment.get(group_index)
            if split_name is None:
                split_exclusions.extend({
                    "source_id": str(records[index].row["source_id"]),
                    "sample_id": records[index].row.get("sample_id"),
                    "reason": "split_capacity",
                } for index in members)
                continue
            for index in members:
                record = records[index]
                source_id = str(record.row["source_id"])
                sample_id = str(record.row.get("sample_id") or hashlib.sha256(
                    f"{source_id}\0{record.row['question']}".encode()).hexdigest()[:16])
                variants = {}
                for resampling in resampling_candidates:
                    candidate_dir = (
                        args.prepared_dir
                        if len(resampling_candidates) == 1
                        else args.prepared_dir / resampling
                    )
                    candidate_path = (candidate_dir / f"{sample_id}.png").resolve()
                    candidate_prep = prepare_image(
                        record.image_path,
                        candidate_path,
                        tuple(args.canvas),
                        resampling,
                    )
                    variants[resampling] = {
                        "path": str(candidate_path),
                        "sha256": sha256_file(candidate_path),
                        "preparation": candidate_prep,
                    }
                primary = variants[resampling_candidates[0]]
                row = {
                    "sample_id": sample_id,
                    "dataset_revision": record.row.get("dataset_revision"),
                    "dataset_usage": record.row.get("dataset_usage"),
                    "training_use_permitted": record.row.get("training_use_permitted"),
                    "source_id": source_id,
                    "source_family": record.row.get("source_family"),
                    "image_type": record.row.get("image_type"),
                    "language": record.row.get("language"),
                    "image_group": sample_group[index],
                    "split": split_name,
                    "question": record.row["question"],
                    "choices": record.row.get("choices"),
                    "reference_answer": record.row["reference_answer"],
                    "answer_type": record.row.get("answer_type", "text"),
                    "answer_precision": record.row.get("answer_precision"),
                    "original_path": str(record.image_path),
                    "original_sha256": record.sha256,
                    "prepared_path": primary["path"],
                    "prepared_sha256": primary["sha256"],
                    "preparation": primary["preparation"],
                    "resampling_candidates": variants,
                }
                output_rows.append(row)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as stream:
            for row in sorted(output_rows, key=lambda item: item["sample_id"]):
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        exclusions_path = args.exclusions or output_path.with_name("exclusions.jsonl")
        if split_exclusions:
            exclusions_path.parent.mkdir(parents=True, exist_ok=True)
            with exclusions_path.open("a", encoding="utf-8") as stream:
                for row in split_exclusions:
                    stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    counts = {name: sum(row["split"] == name for row in output_rows) for name in targets}
    print(json.dumps({"records": len(output_rows), "image_groups": len(groups), "split_counts": counts,
                      "requested_split_counts": effective_targets,
                      "split_exclusions": len(split_exclusions),
                      "seed": args.seed, "output": str(output_path.resolve()),
                      "dataset_revision": MATHVISTA_REVISION if args.mathvista else None,
                      "exclusions": exclusion_counts,
                      "resampling_candidates": resampling_candidates,
                      "dataset_usage": "evaluation_only" if args.mathvista else None,
                      "training_use_permitted": False if args.mathvista else None}, sort_keys=True))
    if args.mathvista:
        print("MathVista permits test-set use and prohibits training-set use; these benchmark examples are marked evaluation-only.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
