"""Subprocess wrapper around `mediakit-cli` (Volcengine AI MediaKit).

    npx @volcengine/mediakit-cli install -y     # Node >= 18
    export MEDIAKIT_API_KEY=...                  # console.volcengine.com/imp/ai-mediakit/settings

Only two cloud tools are used: `video enhance-video` (upscale) and
`editing concat-video` (stitch), plus `shared query-task` to poll them.

Contract (docs/error-codes.md in the CLI repo): the CLI prints ONE JSON object
to stdout. Business failures are signalled *inside* that JSON (`success: false`,
or a terminal `status` of failed/canceled) — the process exit code is only a
secondary signal, so this module parses stdout first and logs the exit code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Callable

from platforms import MEDIAKIT

CLI = os.environ.get("MEDIAKIT_CLI", "mediakit-cli")
FAILED_STATES = {"failed", "canceled", "cancelled"}
TERMINAL_STATES = FAILED_STATES | {"completed"}


class MediaKitError(RuntimeError):
    def __init__(self, message: str, *, argv: list[str] | None = None, stdout: Any = None, stderr: str = ""):
        super().__init__(message)
        self.argv = argv or []
        self.stdout = stdout
        self.stderr = stderr


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("MEDIAKIT_SURFACE", "cli")
    return env


def preflight() -> str:
    """Fail fast (before any paid Ark call) if MediaKit is not usable."""
    if not os.environ.get("MEDIAKIT_API_KEY"):
        raise SystemExit(f"MEDIAKIT_API_KEY is not set — create one at {MEDIAKIT['console']}")
    try:
        proc = subprocess.run([CLI, "version"], capture_output=True, text=True, timeout=30, env=_env())
    except FileNotFoundError:
        raise SystemExit(f"{CLI!r} not found on PATH — install with: npx @volcengine/mediakit-cli install -y")
    version = (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else "?"
    return version


def _parse_stdout(text: str) -> dict:
    """The CLI may prepend a banner or inject an update notice; be lenient."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            pass
    raise ValueError("no JSON object in stdout")


def _run(args: list[str], *, timeout: float) -> dict:
    argv = [CLI, *args]
    _log(f"[mediakit] $ {' '.join(argv)}")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=_env())
    except FileNotFoundError:
        raise SystemExit(f"{CLI!r} not found on PATH — install with: npx @volcengine/mediakit-cli install -y")
    except subprocess.TimeoutExpired:
        raise MediaKitError(f"mediakit-cli timed out after {timeout}s", argv=argv)
    try:
        data = _parse_stdout(proc.stdout)
    except ValueError:
        raise MediaKitError(
            f"mediakit-cli exit {proc.returncode} with non-JSON stdout: {proc.stdout.strip()[:500]!r} "
            f"stderr: {proc.stderr.strip()[:500]!r}",
            argv=argv, stdout=proc.stdout, stderr=proc.stderr,
        )
    if data.get("success") is False or str(data.get("status", "")).lower() in FAILED_STATES or data.get("error"):
        raise MediaKitError(f"mediakit-cli reported failure: {json.dumps(data, ensure_ascii=False)}",
                            argv=argv, stdout=data, stderr=proc.stderr)
    if proc.returncode != 0:
        _log(f"[mediakit] note: exit code {proc.returncode} but stdout JSON carries no failure; continuing")
    return data


def _task_id(data: dict) -> str:
    task_id = data.get("task_id")
    if not task_id:
        raise MediaKitError(f"submission returned no task_id: {data}", stdout=data)
    return str(task_id)


def enhance_video(
    video: str,
    *,
    resolution: str = "1080p",
    scene: str = "aigc",
    tool_version: str = "standard",
    bitrate_level: str = "medium",
    fps: float | None = None,
    client_token: str | None = None,
    timeout: float = 900,
) -> str:
    """Submit a cloud upscale. `video` may be an https URL or a local path (the CLI uploads it)."""
    args = ["--cloud", "video", "enhance-video", "--video-url", video, "--scene", scene,
            "--tool-version", tool_version, "--resolution", resolution, "--bitrate-level", bitrate_level]
    if fps:
        args += ["--fps", str(fps)]
    if client_token:
        args += ["--client-token", client_token]
    return _task_id(_run(args, timeout=timeout))


def concat_video(videos: list[str], *, transitions: list[str] | None = None,
                 client_token: str | None = None, timeout: float = 900) -> str:
    """Submit a cloud concat. Inputs are comma-joined by the CLI flag, so commas are rejected."""
    if not videos:
        raise ValueError("concat_video needs at least one input")
    bad = [v for v in videos if "," in v]
    if bad:
        raise ValueError(f"--video-urls is comma-joined; these inputs contain commas: {bad}")
    args = ["--cloud", "editing", "concat-video", "--video-urls", ",".join(videos)]
    if transitions:
        args += ["--transitions", ",".join(transitions)]
    if client_token:
        args += ["--client-token", client_token]
    return _task_id(_run(args, timeout=timeout))


def query_task(task_id: str, *, timeout: float = 120) -> dict:
    """One status query (we run our own poll loop rather than --poll-complete)."""
    argv = [CLI, "shared", "query-task", "--task-id", task_id]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=_env())
    try:
        return _parse_stdout(proc.stdout)
    except ValueError:
        raise MediaKitError(f"query-task {task_id}: non-JSON stdout {proc.stdout[:300]!r} stderr {proc.stderr[:300]!r}",
                            argv=argv, stdout=proc.stdout, stderr=proc.stderr)


def wait_for_tasks(
    task_ids: list[str],
    *,
    poll_interval: float = 15,
    max_wait: float = 2400,
    on_update: Callable[[str, dict], None] | None = None,
    label: str = "mediakit",
) -> dict[str, dict]:
    """Round-robin poll until every task is completed/failed/canceled."""
    pending = list(dict.fromkeys(task_ids))
    results: dict[str, dict] = {}
    seen: dict[str, str] = {}
    started = time.monotonic()
    while pending:
        for task_id in list(pending):
            try:
                task = query_task(task_id)
            except (MediaKitError, subprocess.TimeoutExpired) as exc:
                _log(f"[{label}] query-task {task_id} failed transiently: {exc}")
                continue
            status = str(task.get("status", "")).lower()
            if seen.get(task_id) != status:
                seen[task_id] = status
                if on_update:
                    on_update(task_id, task)
            if status in TERMINAL_STATES:
                results[task_id] = task
                pending.remove(task_id)
        elapsed = int(time.monotonic() - started)
        _log(f"[{label}] {len(results)}/{len(task_ids)} terminal, {len(pending)} pending (t={elapsed}s)")
        if not pending:
            break
        if elapsed > max_wait:
            raise MediaKitError(f"{label}: still pending after {max_wait}s: {pending}")
        time.sleep(poll_interval)
    return results


def result_url(task: dict) -> str:
    """Completed video tasks flatten result.video_url to the top level; be defensive anyway."""
    nested = task.get("result") if isinstance(task.get("result"), dict) else {}
    for key in ("video_url", "output_url", "url"):
        value = task.get(key) or nested.get(key)
        if value:
            return str(value)
    raise MediaKitError(f"completed task without a recognisable output URL: {json.dumps(task, ensure_ascii=False)}", stdout=task)


def schema(domain: str, tool: str) -> dict:
    argv = [CLI, domain, tool, "--schema"]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=_env())
    try:
        return _parse_stdout(proc.stdout)
    except ValueError:
        raise MediaKitError(f"--schema returned non-JSON: {proc.stdout[:300]!r} {proc.stderr[:300]!r}", argv=argv)
