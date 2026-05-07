---
name: teams-notify
description: Monitor long-running commands or existing process IDs and notify Microsoft Teams through a webhook with run timing and INFO-level logs. Use when the user asks to run a task with Teams notification, watch current execution status, send completion status to Teams, post command logs to a webhook, or wrap a long pipeline/test/build/deploy job with start/end time, duration, and INFO log summary.
---

# Teams Notify

Use this skill to run or monitor a task and send a Teams webhook notification for that task.

## Required Inputs

- Teams webhook URL from the user, or `TEAMS_WEBHOOK_URL` in the environment.
- A command to run, or an existing PID to watch.
- A log file if watching an existing PID and full INFO logs are required.

Never hard-code a webhook into repository files. Treat webhooks as secrets.

## Workflow

1. Identify the task name, command or PID, webhook URL, and where logs should be written.
2. Prefer wrapping new executions with `scripts/teams_notify.py -- ...` so stdout/stderr are captured from the beginning.
3. For an already-running process, use `--pid PID --log-file PATH` when a log file exists. If no log file exists, say that full INFO logs cannot be reconstructed for already-emitted output.
4. Send a Teams notification per task. The final payload must include:
   - `start_time_utc`
   - `end_time_utc`
   - `duration_hh_mm_ss` in `HH/MM/SS`
   - task name
   - exit code when known
   - all captured INFO-level log lines
5. If external webhook posting needs approval in the current environment, request it with the exact destination and payload class. Do not bypass a rejected approval.

## Script Usage

Run a new command and notify on finish:

```bash
python /root/.codex/skills/teams-notify/scripts/teams_notify.py \
  --webhook "$TEAMS_WEBHOOK_URL" \
  --task-name "nightly pipeline" \
  --log-file /tmp/nightly-pipeline.log \
  --notify-start \
  -- \
  python path/to/pipeline.py --config config.yaml
```

Watch an existing PID and notify when it exits:

```bash
python /root/.codex/skills/teams-notify/scripts/teams_notify.py \
  --webhook "$TEAMS_WEBHOOK_URL" \
  --task-name "existing pipeline" \
  --pid 12345 \
  --log-file /tmp/existing-pipeline.log \
  --started-at 2026-05-07T18:30:00Z
```

If `--started-at` is omitted for PID mode, the watcher start time is used.

## INFO Log Handling

The script captures all command output to the log file in command mode. It extracts INFO-level lines by matching common formats such as `INFO:`, `[INFO]`, ` level=INFO`, and lines whose level field is `INFO`.

For existing PIDs, INFO log completeness depends on the supplied `--log-file`. If the process was started in a terminal only, past output is not available unless it was also logged.

## Safety

Teams webhooks can expose private logs outside the local environment. Before sending INFO logs, confirm the user explicitly requested that destination and that the payload can include run logs. If approval rejects sending detailed logs, fall back only to a generic completion message if the user accepts that reduced payload.
