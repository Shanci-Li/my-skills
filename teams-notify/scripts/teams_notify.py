#!/usr/bin/env python3
"""Run or watch a task and notify Microsoft Teams with timing and INFO logs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Optional


INFO_RE = re.compile(
    r"(^|\s|\[|\{)(INFO)(\s|:|\]|\}|=|,|$)|level[=:]\s*INFO",
    re.IGNORECASE,
)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def parse_utc(value: Optional[str]) -> dt.datetime:
    if not value:
        return utc_now()
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def duration_hh_mm_ss(start: dt.datetime, end: dt.datetime) -> str:
    seconds = max(0, int((end - start).total_seconds()))
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}/{minutes:02d}/{seconds:02d}"


def read_info_lines(log_file: Optional[Path]) -> list[str]:
    if log_file is None or not log_file.exists():
        return []
    lines: list[str] = []
    with log_file.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if INFO_RE.search(line):
                lines.append(line)
    return lines


def build_text(
    *,
    task_name: str,
    status: str,
    start: dt.datetime,
    end: Optional[dt.datetime],
    exit_code: Optional[int],
    info_lines: Iterable[str],
    log_file: Optional[Path],
) -> str:
    if end is None:
        duration = "running"
        end_text = "running"
    else:
        duration = duration_hh_mm_ss(start, end)
        end_text = iso_utc(end)

    info_block = "\n".join(info_lines)
    if not info_block:
        info_block = "(no INFO-level log lines captured)"

    exit_text = "unknown" if exit_code is None else str(exit_code)
    log_text = str(log_file) if log_file is not None else "(not captured)"

    return (
        f"Task: {task_name}\n"
        f"Status: {status}\n"
        f"start_time_utc: {iso_utc(start)}\n"
        f"end_time_utc: {end_text}\n"
        f"duration_hh_mm_ss: {duration}\n"
        f"exit_code: {exit_text}\n"
        f"log_file: {log_text}\n"
        "INFO logs:\n"
        f"{info_block}"
    )


def teams_payload(args: argparse.Namespace, text: str) -> dict[str, object]:
    if not args.mention_upn:
        return {"text": text}

    mention_name = args.mention_name or args.mention_upn
    mention_text = f"<at>{mention_name}</at>"
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"{mention_text}\n\n{text}",
                            "wrap": True,
                        }
                    ],
                    "msteams": {
                        "entities": [
                            {
                                "type": "mention",
                                "text": mention_text,
                                "mentioned": {
                                    "id": args.mention_upn,
                                    "name": mention_name,
                                },
                            }
                        ]
                    },
                },
            }
        ],
    }


def post_teams(args: argparse.Namespace, text: str) -> None:
    payload = json.dumps(teams_payload(args, text)).encode("utf-8")
    req = urllib.request.Request(
        args.webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=args.timeout) as resp:
        resp.read()


def run_command(args: argparse.Namespace, command: list[str]) -> int:
    start = utc_now()
    log_file = Path(args.log_file) if args.log_file else None
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)

    if args.notify_start:
        post_teams(
            args,
            build_text(
                task_name=args.task_name,
                status="started",
                start=start,
                end=None,
                exit_code=None,
                info_lines=[],
                log_file=log_file,
            ),
        )

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    log_fh = (
        log_file.open("w", encoding="utf-8", errors="replace")
        if log_file is not None
        else None
    )
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if log_fh is not None:
                log_fh.write(line)
                log_fh.flush()
        exit_code = proc.wait()
    finally:
        if log_fh is not None:
            log_fh.close()

    end = utc_now()
    info_lines = read_info_lines(log_file)
    status = "succeeded" if exit_code == 0 else "failed"
    post_teams(
        args,
        build_text(
            task_name=args.task_name,
            status=status,
            start=start,
            end=end,
            exit_code=exit_code,
            info_lines=info_lines,
            log_file=log_file,
        ),
    )
    return exit_code


def watch_pid(args: argparse.Namespace) -> int:
    start = parse_utc(args.started_at)
    log_file = Path(args.log_file) if args.log_file else None

    if args.notify_start:
        post_teams(
            args,
            build_text(
                task_name=args.task_name,
                status=f"watching PID {args.pid}",
                start=start,
                end=None,
                exit_code=None,
                info_lines=read_info_lines(log_file),
                log_file=log_file,
            ),
        )

    while subprocess.run(
        ["ps", "-p", str(args.pid)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0:
        time.sleep(args.poll_seconds)

    end = utc_now()
    post_teams(
        args,
        build_text(
            task_name=args.task_name,
            status=f"PID {args.pid} exited",
            start=start,
            end=end,
            exit_code=None,
            info_lines=read_info_lines(log_file),
            log_file=log_file,
        ),
    )
    return 0


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--webhook",
        default=os.environ.get("TEAMS_WEBHOOK_URL"),
        help="Teams webhook URL. Defaults to TEAMS_WEBHOOK_URL.",
    )
    parser.add_argument("--task-name", default="Codex task")
    parser.add_argument("--log-file", help="Path where command output is captured/read.")
    parser.add_argument("--pid", type=int, help="Existing process ID to watch.")
    parser.add_argument("--started-at", help="UTC ISO start time for PID mode.")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--timeout", type=int, default=30, help="Webhook timeout seconds.")
    parser.add_argument("--notify-start", action="store_true")
    parser.add_argument(
        "--mention-upn",
        help=(
            "Mention this Teams user by UPN/email. Enables Adaptive Card payload "
            "instead of plain text."
        ),
    )
    parser.add_argument(
        "--mention-name",
        help="Display name for --mention-upn. Defaults to the UPN/email.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if not args.webhook:
        parser.error("--webhook or TEAMS_WEBHOOK_URL is required")

    command = args.command
    if command and command[0] == "--":
        command = command[1:]

    if args.pid is None and not command:
        parser.error("provide either --pid or a command after --")
    if args.pid is not None and command:
        parser.error("provide either --pid or a command, not both")

    return args, command


def main(argv: list[str]) -> int:
    args, command = parse_args(argv)
    try:
        if args.pid is not None:
            return watch_pid(args)
        return run_command(args, command)
    except urllib.error.URLError as exc:
        print(f"Teams webhook failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
