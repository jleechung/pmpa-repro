#!/usr/bin/env python3
"""Aggregate PMPA metric_summary.json files into the paper's result tables.

Walks $PMPA_RUNS/<slug>/<model>/<modality>/<scenario>/<timestamp>/metric_summary.json
(the layout built by repo/eval/injection/injection_cli.py:185), keeps the most
recent run per cell, and reports ISR / S-ASR / C-ASR / utility per harness.

Usage:
  aggregate.py --runs-root "$PMPA_RUNS" [--model qwen3.5-4b]
               [--csv out.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

SCENARIOS = ("calendar", "docs", "form", "email")
MODALITIES = ("txt", "pdf", "png")


def detect_harness(cell_dir: Path, slug: str) -> str:
    """Identify the harness from the cell's own artifacts.

    run-matrix.sh gives both harnesses the same run slug, so they land in the
    same <model>/<modality>/<scenario>/ tree separated only by timestamp.
    Inferring the harness from the slug would label every OpenClaw cell as
    Claude Code. Claude Code sessions carry a transcript copy and a
    `claudecode_session_1_path` field in summary.json; OpenClaw's do not.
    """
    if "openclaw" in slug:
        return "openclaw"
    if "claudecode" in slug:
        return "claudecode"
    summary = cell_dir / "summary.json"
    if summary.is_file():
        try:
            items = json.loads(summary.read_text(encoding="utf-8-sig"))
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return (
                    "claudecode"
                    if "claudecode_session_1_path" in items[0]
                    else "openclaw"
                )
        except Exception:
            pass
    if any(cell_dir.glob("sample_*/claudecode-session_*.jsonl")):
        return "claudecode"
    return "openclaw"


def load_cells(runs_root: Path, model: str | None,
               slug_filter: str | None = None) -> list[dict]:
    cells: dict[tuple, dict] = {}
    # ISR writes <slug>/<model>/<modality>/<scenario>/<timestamp>/ while C-ASR
    # omits the modality level (repo/jobscripts/CASR.py:1398), so dispatch on
    # how many components sit between the runs root and the file.
    for path in runs_root.rglob("metric_summary.json"):
        parts = path.relative_to(runs_root).parts[:-1]
        if len(parts) == 5:
            slug, model_name, modality, scenario, timestamp = parts
        elif len(parts) == 4:
            slug, model_name, scenario, timestamp = parts
            modality = "-"
        else:
            continue
        if model and model_name != model:
            continue
        if "smoke" in slug:
            continue
        # Ablation slugs share the model directory name with the upstream
        # run, so without this they merge into the upstream mean. That made
        # --model qwen3.8-27b report ISR 0.250 instead of the upstream 0.303.
        if slug_filter and slug_filter in slug:
            continue
        mode = "casr" if "casr" in slug else "isr"
        harness = detect_harness(path.parent, slug)
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        # The slug must be part of the key. bin/run-ablation.sh writes
        # calendar cells under its own slug; without the slug those cells
        # share (mode, harness, scenario, modality) with the main ISR cells
        # and, having a later timestamp, would replace them.
        key = (slug, mode, harness, scenario, modality)
        row = {
            "mode": mode,
            "harness": harness,
            "scenario": scenario,
            "modality": modality,
            "timestamp": timestamp,
            "n": payload.get("total_session_count"),
            "isr": payload.get("isr"),
            "s_asr": payload.get("s_asr"),
            "utility": payload.get("utility"),
            "round2_entered": payload.get("round2_entered_count"),
            "path": str(path),
        }
        # Keep the most recent run for a given cell.
        if key not in cells or timestamp > cells[key]["timestamp"]:
            cells[key] = row
    return sorted(
        cells.values(),
        key=lambda r: (r["mode"], r["harness"], r["scenario"], r["modality"]),
    )


def fmt(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.3f}" if isinstance(value, float) else str(value)
    return "-"


def weighted(rows: list[dict], field: str) -> float | None:
    num = den = 0
    for row in rows:
        n, v = row.get("n"), row.get(field)
        if isinstance(n, int) and isinstance(v, (int, float)):
            num += v * n
            den += n
    return num / den if den else None


def print_grid(rows: list[dict]) -> None:
    """Scenario rows, modality columns, one block per mode and harness."""
    for mode in ("isr", "casr"):
        for harness in ("claudecode", "openclaw"):
            sel = [r for r in rows if r["mode"] == mode and r["harness"] == harness]
            if not sel:
                continue
            mods = sorted({r["modality"] for r in sel})
            label = "ISR / S-ASR" if mode == "isr" else "C-ASR"
            print(f"\n**{harness}, {label}**\n")
            print("| scenario | " + " | ".join(mods) + " |")
            print("|---" * (len(mods) + 1) + "|")
            for scenario in SCENARIOS:
                cells = []
                for mod in mods:
                    hit = [r for r in sel
                           if r["scenario"] == scenario and r["modality"] == mod]
                    if not hit:
                        cells.append("-")
                    elif mode == "isr":
                        cells.append(f"{fmt(hit[0]['isr'])} / {fmt(hit[0]['s_asr'])}")
                    else:
                        cells.append(fmt(hit[0]["s_asr"]))
                if any(c != "-" for c in cells):
                    print(f"| {scenario} | " + " | ".join(cells) + " |")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--skip-slug",
        default="natimg",
        help="Drop run slugs containing this string. The native-image "
             "ablation is dropped by default; pass an empty string to keep it.",
    )
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument(
        "--grid",
        action="store_true",
        help="Print the scenario-by-modality grid per harness, as markdown.",
    )
    args = parser.parse_args()

    rows = load_cells(args.runs_root, args.model, args.skip_slug or None)
    if not rows:
        print("no metric_summary.json found")
        return 1

    for mode in ("isr", "casr"):
        mode_rows = [r for r in rows if r["mode"] == mode]
        if not mode_rows:
            continue
        label = "ISR / S-ASR (injection)" if mode == "isr" else "C-ASR (trigger)"
        print(f"\n=== {label} ===")
        print(f"{'harness':<12} {'scenario':<9} {'mod':<4} {'n':>4} "
              f"{'ISR':>7} {'S-ASR':>7} {'util':>7}")
        for r in mode_rows:
            print(f"{r['harness']:<12} {r['scenario']:<9} {r['modality']:<4} "
                  f"{fmt(r['n']):>4} {fmt(r['isr']):>7} {fmt(r['s_asr']):>7} "
                  f"{fmt(r['utility']):>7}")
        for harness in ("claudecode", "openclaw"):
            hrows = [r for r in mode_rows if r["harness"] == harness]
            if not hrows:
                continue
            total = sum(r["n"] for r in hrows if isinstance(r["n"], int))
            print(f"  {harness:<10} cells={len(hrows):<3} n={total:<5} "
                  f"ISR={fmt(weighted(hrows,'isr'))} "
                  f"S-ASR={fmt(weighted(hrows,'s_asr'))} "
                  f"util={fmt(weighted(hrows,'utility'))}")

    if args.grid:
        print_grid(rows)
        return 0

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
