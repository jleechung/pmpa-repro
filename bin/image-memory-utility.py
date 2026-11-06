#!/usr/bin/env python3
"""Compare model answers for original and prepared benchmark images."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from image_memory_scoring import score_answer

MODEL_REVISION = "8c0b3d30f3ae96f5c7f774de704bf0ada83e422a"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix, "application/octet-stream")


def prompt_for(row: dict[str, Any]) -> str:
    choices = row.get("choices")
    text = f"Question: {row['question']}\n"
    if isinstance(choices, list) and choices:
        text += "Choices:\n" + "\n".join(f"{chr(65 + i)}. {choice}" for i, choice in enumerate(choices)) + "\n"
    text += "Answer the visual question. Return only the final answer, with no explanation."
    return text


def request_answer(endpoint: str, model: str, api_key: str, image_path: Path,
                   prompt: str, timeout: float) -> dict[str, Any]:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 2048,
        "output_config": {"effort": "xhigh"},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {
                    "type": "base64", "media_type": mime_type(image_path),
                    "data": encoded,
                }},
            ],
        }],
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read()
        result = json.loads(response_body)
        content = result.get("content")
        text_blocks = [part.get("text", "") for part in content
                       if isinstance(part, dict) and part.get("type") == "text"] if isinstance(content, list) else []
        answer = "\n".join(text for text in text_blocks if isinstance(text, str)).strip()
        if not answer:
            raise ValueError(
                f"completion has no visible text; stop_reason={result.get('stop_reason')}"
            )
        return {"answer": answer.strip(), "error": None,
                "finish_reason": result.get("stop_reason"),
                "reasoning_effort": "xhigh",
                "duration_seconds": time.time() - started}
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"answer": None, "error": f"{type(exc).__name__}: {exc}", "duration_seconds": time.time() - started}


def read_manifest(path: Path, split: str) -> list[dict[str, Any]]:
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
                raise ValueError(f"{path}:{line_number}: each manifest row must be an object")
            if row.get("split") == split:
                rows.append(row)
    if not rows:
        raise ValueError(f"Manifest has no rows for split {split!r}")
    return rows


def candidate_artifact(row: dict[str, Any], candidate: str) -> tuple[Path, str | None]:
    if candidate == "original":
        return Path(row["original_path"]).expanduser().resolve(), row.get("original_sha256")
    if candidate == "prepared":
        return Path(row["prepared_path"]).expanduser().resolve(), row.get("prepared_sha256")
    variants = row.get("resampling_candidates")
    if not isinstance(variants, dict) or candidate not in variants:
        raise ValueError(f"manifest row has no resampling candidate {candidate!r}")
    variant = variants[candidate]
    if not isinstance(variant, dict) or not isinstance(variant.get("path"), str):
        raise ValueError(f"resampling candidate {candidate!r} has no image path")
    expected_hash = variant.get("sha256")
    if expected_hash is not None and not isinstance(expected_hash, str):
        raise ValueError(f"resampling candidate {candidate!r} has an invalid SHA-256 value")
    return Path(variant["path"]).expanduser().resolve(), expected_hash


def selected_candidate(path: Path, manifest: Path, split: str) -> str:
    try:
        selection = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read selection file {path}: {exc}") from exc
    if not isinstance(selection, dict):
        raise ValueError("selection file must contain a JSON object")
    candidate = selection.get("selected_candidate")
    if not isinstance(candidate, str) or not candidate:
        raise ValueError("selection file has no selected_candidate")
    expected_manifest_hash = selection.get("manifest_sha256")
    if expected_manifest_hash and expected_manifest_hash != sha256_file(manifest):
        raise ValueError("selection file was created from a different manifest")
    if split == "held_out" and selection.get("accepted") is not True:
        raise ValueError("held-out evaluation requires an accepted validation selection")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_jsonl", type=Path)
    parser.add_argument("--endpoint", required=True, help="Anthropic-compatible server base URL, such as http://127.0.0.1:8000/v1")
    parser.add_argument("--model", required=True, help="Served model name")
    parser.add_argument("--model-revision", default=MODEL_REVISION)
    parser.add_argument("--api-key", default="EMPTY")
    parser.add_argument("--split", default="validation", choices=("development", "validation", "held_out"))
    parser.add_argument(
        "--candidate",
        action="append",
        help=(
            "Evaluate this prepared candidate beside the original image. Repeat the option "
            "for several candidates. The default compares original and prepared."
        ),
    )
    parser.add_argument(
        "--selection-file",
        type=Path,
        help="Evaluate the candidate chosen by an earlier selection report.",
    )
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--limit", type=int, help="Optional maximum number of validation questions")
    args = parser.parse_args()
    if args.timeout <= 0 or args.limit is not None and args.limit <= 0:
        parser.error("timeout and limit must be positive")
    if args.candidate and args.selection_file:
        parser.error("use either --candidate or --selection-file")
    try:
        rows = read_manifest(args.manifest, args.split)
        if args.selection_file:
            candidates = [selected_candidate(
                args.selection_file.resolve(), args.manifest.resolve(), args.split
            )]
        else:
            candidates = list(dict.fromkeys(args.candidate or ("prepared",)))
        if "original" in candidates:
            raise ValueError("original is included automatically and cannot be a candidate")
        if args.split == "held_out" and args.candidate:
            raise ValueError("held-out candidate evaluation requires --selection-file")
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.limit is not None:
        rows = rows[:args.limit]
    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as output:
        for index, row in enumerate(rows):
            prompt = prompt_for(row)
            for view in ("original", *candidates):
                try:
                    image_path, expected_hash = candidate_artifact(row, view)
                except (KeyError, TypeError, ValueError) as exc:
                    print(f"sample {row.get('sample_id')}: {exc}", file=sys.stderr)
                    return 2
                result = {
                    "sample_id": row.get("sample_id"),
                    "split": row.get("split"),
                    "view": view,
                    "image_path": str(image_path),
                    "image_sha256": None,
                    "dataset_revision": row.get("dataset_revision"),
                    "question": row.get("question"),
                    "model": args.model,
                    "model_revision": args.model_revision,
                    "endpoint": args.endpoint,
                    "answer_type": row.get("answer_type"),
                    "answer_precision": row.get("answer_precision"),
                    "reference_answer": row.get("reference_answer"),
                    "prediction": None,
                    "extracted_answer": None,
                    "correct": False,
                    "error": None,
                }
                try:
                    result["image_sha256"] = sha256_file(image_path)
                    if expected_hash and result["image_sha256"] != expected_hash:
                        raise ValueError(f"{view} image hash does not match the manifest")
                    completion = request_answer(args.endpoint, args.model, args.api_key,
                                                image_path, prompt, args.timeout)
                    result["prediction"] = completion["answer"]
                    result["error"] = completion["error"]
                    result["duration_seconds"] = completion["duration_seconds"]
                    result["finish_reason"] = completion.get("finish_reason")
                    result["reasoning_effort"] = completion.get("reasoning_effort", "xhigh")
                    extracted, correct = score_answer(completion["answer"], row)
                    result["extracted_answer"] = extracted
                    result["correct"] = correct
                except (OSError, KeyError, TypeError, ValueError) as exc:
                    result["error"] = f"{type(exc).__name__}: {exc}"
                output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
                output.flush()
            print(f"completed {index + 1}/{len(rows)} questions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
