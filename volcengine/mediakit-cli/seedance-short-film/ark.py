"""Thin synchronous client for the three Ark endpoints this example uses.

    chat()               POST /chat/completions           (screenplay)
    generate_image()     POST /images/generations         (Seedream character sheets)
    create_video_task()  POST /contents/generations/tasks (Seedance 2.5, async)
    get_video_task()     GET  /contents/generations/tasks/{id}

Configuration is read from the environment at import time:

    export ARK_API_KEY=...           # Volcano Engine ModelArk key (console.volcengine.com/ark)
    export ARK_LLM_MODEL=...         # optional: override the chat model id only

Retry policy (lifted from creative-storyboard's renderer): retry only on 429,
5xx and transport errors; never on other 4xx; and never retry a *timed-out
task-creation POST*, because the server may have accepted it and a retry would
create a second billed generation.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import requests

import config

BASE_URL = config.ARK_BASE_URL
VIDEO_MODEL = config.VIDEO_MODEL
IMAGE_MODEL = config.IMAGE_MODEL
LLM_MODEL = os.environ.get("ARK_LLM_MODEL") or config.LLM_MODEL

TERMINAL = {"succeeded", "failed", "expired", "cancelled"}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ArkError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class ArkTimeout(ArkError):
    pass


def _api_key() -> str:
    try:
        return os.environ["ARK_API_KEY"]
    except KeyError:
        raise SystemExit("ARK_API_KEY is not set (get one from the Volcano Engine ModelArk console)")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        # Guards against a retried POST creating a second billed generation.
        "X-Client-Request-Id": f"mediakit_examples/{uuid.uuid4()}",
    }


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    timeout: float,
    attempts: int = 3,
    retry_timeouts: bool = True,
    backoff: float = 5.0,
) -> dict:
    url = f"{BASE_URL}{path}"
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.request(method, url, headers=_headers(), json=body, timeout=timeout)
        except requests.Timeout as exc:
            last = exc
            if not retry_timeouts:
                raise ArkTimeout(f"{method} {path} timed out after {timeout}s (not retried: may already be accepted)")
        except requests.RequestException as exc:  # transport error
            last = exc
        else:
            if resp.status_code < 400:
                return resp.json() if resp.content else {}
            try:
                payload: Any = resp.json()
            except ValueError:
                payload = resp.text
            if resp.status_code not in RETRYABLE_STATUS:
                raise ArkError(f"{method} {path} -> HTTP {resp.status_code}: {payload}", status=resp.status_code, body=payload)
            last = ArkError(f"HTTP {resp.status_code}: {payload}", status=resp.status_code, body=payload)
        if attempt < attempts:
            _log(f"[ark] {method} {path} attempt {attempt} failed ({last}); retrying in {backoff}s")
            time.sleep(backoff)
    raise ArkError(f"{method} {path} failed after {attempts} attempts: {last}")


# --------------------------------------------------------------------------- chat


def chat(messages: list[dict], *, temperature: float = 0.7, timeout: float = 300) -> str:
    """One chat completion; returns the assistant text."""
    data = _request(
        "POST",
        "/chat/completions",
        {"model": LLM_MODEL, "messages": messages, "temperature": temperature},
        timeout=timeout,
    )
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ArkError(f"unexpected chat response shape: {data}", body=data)


# -------------------------------------------------------------------------- images


def generate_image(prompt: str, *, size: str = "1536x2048", timeout: float = 300) -> str:
    """Synchronous Seedream call; returns a presigned URL valid for 24 hours."""
    data = _request(
        "POST",
        "/images/generations",
        {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            "size": size,               # explicit WxH so the model cannot pick its own aspect ratio
            "output_format": "png",
            "watermark": False,
            "response_format": "url",
        },
        timeout=timeout,
    )
    try:
        return data["data"][0]["url"]
    except (KeyError, IndexError, TypeError):
        raise ArkError(f"unexpected image response shape: {data}", body=data)


# --------------------------------------------------------------------------- video


def build_video_body(
    prompt: str,
    reference_image_urls: list[str],
    *,
    ratio: str,
    duration: int,
    resolution: str,
    generate_audio: bool,
    execution_expires_after: int = 3600,
) -> dict:
    """The exact Seedance request body (also printed by --dry-run).

    References use role "reference_image" only. "first_frame" would force
    ratio=adaptive, and edit/extend wording in the prompt would force
    ratio=adaptive / duration=-1 (see screenplay.FORBIDDEN).
    """
    content: list[dict] = [{"type": "text", "text": prompt}]
    for url in reference_image_urls:
        content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    return {
        "model": VIDEO_MODEL,
        "content": content,
        "ratio": ratio,
        "duration": duration,
        "resolution": resolution,
        "generate_audio": generate_audio,
        "omni_reference_task_type": "auto",
        "output_format": "mp4",
        "watermark": False,
        "execution_expires_after": execution_expires_after,
    }


def create_video_task(body: dict, *, timeout: float = 30) -> str:
    """Submit one Seedance task; returns its id. A timed-out POST is *not* retried."""
    data = _request("POST", "/contents/generations/tasks", body, timeout=timeout, retry_timeouts=False)
    try:
        return data["id"]
    except (KeyError, TypeError):
        raise ArkError(f"unexpected task creation response: {data}", body=data)


def get_video_task(task_id: str, *, timeout: float = 30) -> dict:
    return _request("GET", f"/contents/generations/tasks/{task_id}", timeout=timeout)


def video_url_of(task: dict) -> str | None:
    content = task.get("content") or {}
    return content.get("video_url") or content.get("url")


def wait_for_video_tasks(
    task_ids: list[str],
    *,
    poll_interval: float = 15,
    max_wait: float = 1800,
    on_update: Callable[[str, dict], None] | None = None,
    label: str = "seedance",
) -> dict[str, dict]:
    """Poll every pending task each tick until all are terminal.

    on_update(task_id, task_json) fires on every status change so the caller can
    persist progress. Raises ArkTimeout (listing the pending ids) after max_wait.
    """
    pending = list(dict.fromkeys(task_ids))
    results: dict[str, dict] = {}
    seen_status: dict[str, str] = {}
    started = time.monotonic()
    while pending:
        for task_id in list(pending):
            try:
                task = get_video_task(task_id)
            except ArkError as exc:
                _log(f"[{label}] GET {task_id} failed transiently: {exc}")
                continue
            status = task.get("status", "")
            if seen_status.get(task_id) != status:
                seen_status[task_id] = status
                if on_update:
                    on_update(task_id, task)
            if status in TERMINAL:
                results[task_id] = task
                pending.remove(task_id)
        elapsed = int(time.monotonic() - started)
        done = len(results)
        _log(f"[{label}] {done}/{len(task_ids)} terminal, {len(pending)} pending (t={elapsed}s)")
        if not pending:
            break
        if elapsed > max_wait:
            raise ArkTimeout(f"{label}: still pending after {max_wait}s: {pending}")
        time.sleep(poll_interval)
    return results


# ------------------------------------------------------------------------ download


def download(url: str, dest: Path, *, timeout: float = 600) -> Path:
    """Stream url to dest (atomically). Skips if dest already exists and is non-empty."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with requests.get(url, stream=True, timeout=timeout) as resp:
        if resp.status_code in (403, 404):
            raise ArkError(
                f"download of {dest.name} got HTTP {resp.status_code}: the presigned URL has probably "
                "expired (Ark URLs live 24h). Regenerate this asset.",
                status=resp.status_code,
            )
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)
    os.replace(tmp, dest)
    return dest
