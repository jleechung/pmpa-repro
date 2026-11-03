#!/usr/bin/env python3
"""Inspect what Claude Code actually sends, without a GPU.

Stands up a stub Anthropic /v1/messages endpoint, points `claude --print` at it,
captures the request body, and reports whether the harness advertises its
file-based memory (directory path + memory tooling) to the model.

This exists because the initial smoke run scored ISR=0 for Claude Code while the
model's own output claimed it had written a memory note: it had in fact called
the generic Write tool against a path it invented inside the project cwd. The
open question is whether the harness ever told it where memory lives.

Usage:
  probe-system-prompt.py [--settings-json '<json>'] [--port 8973]
                         [--cwd DIR] [--config-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CAPTURED: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        try:
            CAPTURED.append(json.loads(raw))
        except json.JSONDecodeError:
            CAPTURED.append({"_unparsed": raw.decode("utf-8", "replace")})
        body = json.dumps(
            {
                "id": "msg_probe",
                "type": "message",
                "role": "assistant",
                "model": "probe",
                "content": [{"type": "text", "text": "PROBE_OK"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - /v1/models probes
        body = json.dumps({"data": [{"id": "probe"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence per-request logging
        pass


def system_text(payload: dict) -> str:
    system = payload.get("system")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n".join(
            block.get("text", "") for block in system if isinstance(block, dict)
        )
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8973)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument(
        "--settings-json",
        default=None,
        help="Extra settings passed to `claude --settings` for this run.",
    )
    parser.add_argument("--prompt", default="Remember that I like short replies.")
    parser.add_argument(
        "--contains",
        action="append",
        default=None,
        help="Report whether this string appears anywhere in the request "
             "(system prompt or messages). Repeatable. Use to check whether "
             "seeded memory contents are actually surfaced to the model.",
    )
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = os.environ.copy()
    env.update(
        {
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{args.port}",
            "ANTHROPIC_AUTH_TOKEN": "probe",
            "ANTHROPIC_MODEL": "probe",
            "CLAUDE_CONFIG_DIR": str(args.config_dir),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    env.pop("ANTHROPIC_API_KEY", None)

    command = ["claude", "--print", "--dangerously-skip-permissions"]
    if args.settings_json:
        command += ["--settings", args.settings_json]
    command.append(args.prompt)

    proc = subprocess.run(
        command, cwd=str(args.cwd), env=env, capture_output=True, text=True, timeout=180
    )
    server.shutdown()

    print(f"claude exit={proc.returncode}")
    if proc.returncode != 0:
        print("stderr:", proc.stderr[:800])
    if not CAPTURED:
        print("NO REQUEST CAPTURED — claude never called the endpoint")
        return 1

    print(f"requests captured: {len(CAPTURED)}")
    # Claude Code makes auxiliary calls (titles, classifiers) alongside the
    # agent turn; the agent turn is the one carrying the tool definitions.
    best = max(CAPTURED, key=lambda p: len(p.get("tools", []) or []))
    for index, payload in enumerate(CAPTURED):
        tools = [t.get("name") for t in payload.get("tools", []) if isinstance(t, dict)]
        marker = " <- agent turn" if payload is best else ""
        print(
            f"  [{index}] system={len(system_text(payload))}c "
            f"tools={len(tools)}{marker}"
        )

    text = system_text(best)
    tools = [t.get("name") for t in best.get("tools", []) if isinstance(t, dict)]
    print(f"\nagent turn: system prompt chars: {len(text)}")
    print(f"tools offered ({len(tools)}): {sorted(n for n in tools if n)}")
    print(f"memory-ish tools: {[n for n in tools if n and 'emory' in n]}")
    for needle in ("MEMORY.md", "file-based memory", "persistent memory", "/memory"):
        print(f"  system mentions {needle!r}: {needle in text}")
    paths = sorted(set(re.findall(r"[^\s\"'`]*memory[^\s\"'`]*", text)))
    print(f"  memory paths in system prompt: {paths[:8] or 'NONE'}")

    if args.contains:
        whole = json.dumps(best)
        print("\nwhole-request substring checks:")
        for needle in args.contains:
            where = []
            if needle in text:
                where.append("system")
            if needle in whole:
                where.append("request")
            print(f"  {needle!r}: {' + '.join(where) if where else 'ABSENT'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
