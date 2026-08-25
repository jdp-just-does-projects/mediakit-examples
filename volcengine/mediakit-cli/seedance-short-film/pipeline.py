"""Idea -> screenplay -> character sheets -> Seedance 2.5 clips -> MediaKit upscale -> MediaKit stitch.

    export ARK_PLATFORM=byteplus          # or: volcengine  (Seedance / Seedream / LLM)
    export ARK_API_KEY=...                # that platform's ModelArk key
    export MEDIAKIT_API_KEY=...           # Volcengine AI MediaKit key (upscale + concat)
    python pipeline.py --idea "A lighthouse keeper befriends a storm." --dry-run
    python pipeline.py --idea "A lighthouse keeper befriends a storm."

Every step is resumable: state lives in <out>/state.json and paid outputs are
never regenerated if they already exist. Re-run the same command to continue.
Progress goes to stderr; the final summary JSON goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ark
import mediakit
import screenplay as sp_mod

STEPS = ["screenplay", "characters", "shots", "enhance", "concat"]
STATE_VERSION = 1
REF_URL_MAX_AGE_S = 20 * 3600  # Ark presigned URLs live 24h; leave headroom for the Seedance job to fetch them
PLACEHOLDER_URL = "https://example.invalid/{id}.png"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------------------ args


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--idea", help="one-line story idea (required unless --screenplay or resuming)")
    p.add_argument("--out", help="run directory (default: runs/<timestamp>-<slug>)")
    p.add_argument("--shots", type=int, default=4)
    p.add_argument("--shot-seconds", type=int, default=24, help="Seedance 2.5 accepts 4..30")
    p.add_argument("--ratio", default="16:9")
    p.add_argument("--seedance-resolution", default="480p", choices=["480p", "720p", "1080p"])
    p.add_argument("--enhance-resolution", default="1080p", help="MediaKit target: 240p..1080p, 2k, 4k")
    p.add_argument("--enhance-scene", default="aigc", choices=["common", "ugc", "short_series", "aigc", "old_film"])
    p.add_argument("--enhance-tool-version", default="standard", choices=["standard", "professional"])
    p.add_argument("--bitrate-level", default="medium", choices=["low", "medium", "high"])
    p.add_argument("--no-audio", action="store_true", help="generate_audio=false for every shot")
    p.add_argument("--transitions", help="comma-separated MediaKit transition ids, e.g. 1182359")
    p.add_argument("--concat-from-local", action="store_true", help="upload enhanced/*.mp4 instead of passing MediaKit URLs")
    p.add_argument("--skip-enhance", action="store_true", help="stitch the raw 480p clips (debugging)")
    p.add_argument("--screenplay", help="use this screenplay.json instead of asking the LLM")
    p.add_argument("--style", help="style direction handed to the LLM")
    p.add_argument("--max-characters", type=int, default=3)
    p.add_argument("--dry-run", action="store_true", help="screenplay + prompts + request bodies only; no paid media calls")
    p.add_argument("--until", choices=STEPS, help="stop after this step")
    p.add_argument("--fresh", action="store_true", help="discard an existing run directory (needs --yes if non-empty)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--retry-failed", action="store_true", help="resubmit shots/tasks whose last attempt failed")
    p.add_argument("--poll-interval", type=float, default=15)
    p.add_argument("--seedance-max-wait", type=float, default=1800)
    p.add_argument("--mediakit-max-wait", type=float, default=2400)
    p.add_argument("--mediakit-schema", action="store_true", help="print enhance-video / concat-video --schema and exit")
    return p.parse_args(argv)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "run").lower()).strip("-")[:40] or "run"


# ----------------------------------------------------------------------- state


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_state(out: Path, state: dict) -> None:
    tmp = out / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.replace(tmp, out / "state.json")


def write_log(out: Path, name: str, data) -> None:
    (out / "log").mkdir(parents=True, exist_ok=True)
    (out / "log" / name).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def config_of(args) -> dict:
    return {
        "shots": args.shots, "shot_seconds": args.shot_seconds, "ratio": args.ratio,
        "seedance_resolution": args.seedance_resolution, "enhance_resolution": args.enhance_resolution,
        "enhance_scene": args.enhance_scene, "enhance_tool_version": args.enhance_tool_version,
        "bitrate_level": args.bitrate_level, "generate_audio": not args.no_audio,
        "transitions": [t.strip() for t in args.transitions.split(",")] if args.transitions else [],
    }


def load_or_init_state(out: Path, args) -> dict:
    path = out / "state.json"
    if args.fresh and out.exists() and any(out.iterdir()):
        if not args.yes:
            raise SystemExit(f"{out} is not empty; --fresh would discard paid outputs. Add --yes to confirm.")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    if path.exists():
        state = json.loads(path.read_text())
        if state.get("version") != STATE_VERSION:
            raise SystemExit(f"{path} has state version {state.get('version')}, expected {STATE_VERSION}")
        if state.get("ark_platform") != ark.PLATFORM:
            raise SystemExit(f"run was started on ARK_PLATFORM={state.get('ark_platform')}, now {ark.PLATFORM}")
        stale = {k: (state["config"].get(k), v) for k, v in config_of(args).items()
                 if k in ("shots", "shot_seconds", "ratio", "seedance_resolution", "generate_audio")
                 and state["config"].get(k) != v}
        if stale:
            raise SystemExit(f"resume config mismatch (state vs flags): {stale}. Use a new --out or --fresh.")
        state["config"].update(config_of(args))  # enhance/concat knobs may change between resumes
        log(f"[pipeline] resuming run {state['run_id']} in {out}")
        return state
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug(args.idea)[:20]}"
    state = {
        "version": STATE_VERSION, "run_id": run_id, "created_at": now_iso(), "ark_platform": ark.PLATFORM,
        "models": {"video": ark.VIDEO_MODEL, "image": ark.IMAGE_MODEL, "llm": ark.LLM_MODEL},
        "config": config_of(args), "idea": args.idea, "screenplay_path": "screenplay.json",
        "characters": {}, "shots": [], "concat": {}, "steps_done": [],
    }
    save_state(out, state)
    log(f"[pipeline] new run {run_id} in {out}")
    return state


def mark_done(out: Path, state: dict, step: str) -> None:
    if step not in state["steps_done"]:
        state["steps_done"].append(step)
    save_state(out, state)


# ------------------------------------------------------------------ screenplay


def step_screenplay(out: Path, state: dict, args) -> sp_mod.Screenplay:
    path = out / state["screenplay_path"]
    cfg = state["config"]
    if args.screenplay and not path.exists():
        shutil.copy(args.screenplay, path)
        log(f"[screenplay] using {args.screenplay}")
    if path.exists():
        sp = sp_mod.from_json(json.loads(path.read_text()))
        violations = sp_mod.validate(sp, shots=cfg["shots"], shot_seconds=cfg["shot_seconds"], max_characters=99)
        if violations:
            raise SystemExit(f"{path} fails validation:\n  - " + "\n  - ".join(violations))
        sp_mod.compose_all(sp)  # only shots whose seedance_prompt is empty (hand edits stick)
    else:
        if not args.idea:
            raise SystemExit("--idea is required for a new run (or pass --screenplay)")
        sp = sp_mod.write_screenplay(
            args.idea, shots=cfg["shots"], shot_seconds=cfg["shot_seconds"], ratio=cfg["ratio"],
            max_characters=args.max_characters, style_hint=args.style, chat=ark.chat,
        )
    path.write_text(json.dumps(sp_mod.to_json(sp), indent=2, ensure_ascii=False))

    if not state["shots"]:
        state["shots"] = [{"index": s.index, "duration": s.duration or cfg["shot_seconds"],
                           "reference_ids": s.reference_ids, "seedance": {}, "enhance": {}} for s in sp.shots]
    for entry, s in zip(state["shots"], sp.shots):
        entry["reference_ids"] = s.reference_ids
    state["title"] = sp.title
    mark_done(out, state, "screenplay")
    log(f"[screenplay] \"{sp.title}\" — {len(sp.characters)} characters, {len(sp.shots)} shots -> {path}")
    return sp


def print_dry_run(sp: sp_mod.Screenplay, state: dict) -> None:
    cfg = state["config"]
    portraits = {c.id: sp_mod.portrait_prompt(c, sp.style_bible) for c in sp.characters}
    requests_ = []
    for s in sp.shots:
        refs = [PLACEHOLDER_URL.format(id=cid) for cid in s.reference_ids]
        requests_.append(ark.build_video_body(s.seedance_prompt, refs, ratio=cfg["ratio"], duration=s.duration,
                                              resolution=cfg["seedance_resolution"], generate_audio=cfg["generate_audio"]))
    print(json.dumps({
        "screenplay": sp_mod.to_json(sp),
        "seedream_requests": {cid: {"model": ark.IMAGE_MODEL, "prompt": p, "size": "1536x2048"} for cid, p in portraits.items()},
        "seedance_requests": requests_,
        "mediakit": {
            "enhance": f"mediakit-cli --cloud video enhance-video --video-url shots/shot_N.mp4 --scene {cfg['enhance_scene']} "
                       f"--tool-version {cfg['enhance_tool_version']} --resolution {cfg['enhance_resolution']} --bitrate-level {cfg['bitrate_level']}",
            "concat": "mediakit-cli --cloud editing concat-video --video-urls <enhanced urls>"
                      + (f" --transitions {','.join(cfg['transitions'])}" if cfg["transitions"] else ""),
        },
    }, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------ characters


def generate_portrait(out: Path, state: dict, sp: sp_mod.Screenplay, c: sp_mod.Character) -> dict:
    prompt = sp_mod.portrait_prompt(c, sp.style_bible)
    log(f"[characters] Seedream: {c.id} ({c.name})")
    url = ark.generate_image(prompt)
    local = out / "characters" / f"{c.id}.png"
    ark.download(url, local)
    entry = {"prompt": prompt, "url": url, "url_issued_at": time.time(), "local_path": str(local.relative_to(out))}
    state["characters"][c.id] = entry
    save_state(out, state)
    return entry


def step_characters(out: Path, state: dict, sp: sp_mod.Screenplay) -> None:
    for c in sp.characters:
        entry = state["characters"].get(c.id)
        if entry and (out / entry["local_path"]).exists() and entry.get("url"):
            log(f"[characters] {c.id}: reusing {entry['local_path']}")
            continue
        generate_portrait(out, state, sp, c)
    mark_done(out, state, "characters")


def fresh_reference_url(out: Path, state: dict, sp: sp_mod.Screenplay, cid: str) -> str:
    entry = state["characters"][cid]
    age = time.time() - entry.get("url_issued_at", 0)
    if age > REF_URL_MAX_AGE_S:
        log(f"[characters] WARNING: {cid}'s reference URL is {age/3600:.1f}h old (Ark URLs expire at 24h). "
            "Regenerating the portrait — shots rendered earlier used a different sheet, so consistency may break.")
        entry = generate_portrait(out, state, sp, sp.character(cid))
    return entry["url"]


# ----------------------------------------------------------------------- shots


def step_shots(out: Path, state: dict, sp: sp_mod.Screenplay, args) -> None:
    cfg = state["config"]
    to_submit, to_poll = [], []
    for entry, shot in zip(state["shots"], sp.shots):
        sd = entry["seedance"]
        local = out / "shots" / f"shot_{shot.index}.mp4"
        if local.exists() and local.stat().st_size > 0:
            sd["local_path"] = str(local.relative_to(out))
            continue
        if sd.get("task_id") and sd.get("status") not in {"failed", "expired", "cancelled"}:
            to_poll.append(entry)
        elif sd.get("task_id") and not args.retry_failed:
            raise SystemExit(f"shot {shot.index} task {sd['task_id']} ended {sd['status']}: {sd.get('error')}\n"
                             "Re-run with --retry-failed to submit it again (billed).")
        else:
            to_submit.append((entry, shot))
    save_state(out, state)

    for entry, shot in to_submit:
        refs = [fresh_reference_url(out, state, sp, cid) for cid in shot.reference_ids]
        body = ark.build_video_body(shot.seedance_prompt, refs, ratio=cfg["ratio"], duration=shot.duration,
                                    resolution=cfg["seedance_resolution"], generate_audio=cfg["generate_audio"])
        write_log(out, f"ark_shot_{shot.index}_request.json", body)
        task_id = ark.create_video_task(body)
        entry["seedance"] = {"task_id": task_id, "submitted_at": now_iso(), "status": "submitted",
                             "attempt": entry["seedance"].get("attempt", 0) + 1}
        save_state(out, state)
        log(f"[seedance] shot {shot.index}: task {task_id} ({shot.duration}s, {cfg['seedance_resolution']}, {len(refs)} refs)")
        to_poll.append(entry)

    if not to_poll:
        mark_done(out, state, "shots")
        return
    by_task = {e["seedance"]["task_id"]: e for e in to_poll}

    def on_update(task_id: str, task: dict) -> None:
        e = by_task[task_id]["seedance"]
        e["status"] = task.get("status")
        if url := ark.video_url_of(task):
            e["video_url"] = url
        if task.get("error"):
            e["error"] = task["error"]
        save_state(out, state)

    results = ark.wait_for_video_tasks(list(by_task), poll_interval=args.poll_interval,
                                       max_wait=args.seedance_max_wait, on_update=on_update)
    failures = []
    for task_id, task in results.items():
        entry = by_task[task_id]
        write_log(out, f"ark_shot_{entry['index']}_result.json", task)
        if task.get("status") == "succeeded" and (url := ark.video_url_of(task)):
            local = out / "shots" / f"shot_{entry['index']}.mp4"
            ark.download(url, local)
            entry["seedance"]["local_path"] = str(local.relative_to(out))
            log(f"[seedance] shot {entry['index']}: downloaded {local.name} ({local.stat().st_size/1e6:.1f} MB)")
        else:
            failures.append(f"shot {entry['index']} ({task_id}): {task.get('status')} {task.get('error')}")
        save_state(out, state)
    if failures:
        raise SystemExit("[seedance] failed tasks:\n  " + "\n  ".join(failures) + "\nRe-run with --retry-failed to resubmit them.")
    mark_done(out, state, "shots")


# --------------------------------------------------------------------- enhance


def step_enhance(out: Path, state: dict, args) -> None:
    cfg = state["config"]
    if args.skip_enhance:
        for entry in state["shots"]:
            entry["enhance"] = {"skipped": True, "local_path": entry["seedance"]["local_path"]}
        mark_done(out, state, "enhance")
        log("[enhance] skipped (--skip-enhance)")
        return
    to_poll = []
    for entry in state["shots"]:
        en = entry["enhance"]
        n = entry["index"]
        local = out / "enhanced" / f"shot_{n}.mp4"
        if local.exists() and local.stat().st_size > 0:
            en["local_path"] = str(local.relative_to(out))
            continue
        if en.get("task_id") and en.get("status") not in mediakit.FAILED_STATES:
            to_poll.append(entry)
            continue
        if en.get("task_id") and not args.retry_failed:
            raise SystemExit(f"enhance of shot {n} (task {en['task_id']}) ended {en['status']}: {en.get('error')}\n"
                             "Re-run with --retry-failed to submit it again.")
        attempt = en.get("attempt", 0) + 1
        token = f"{state['run_id']}-enh-{n}" + (f"-r{attempt}" if attempt > 1 else "")
        src = str(out / entry["seedance"]["local_path"])  # local file: the CLI uploads it (cached by path+size+mtime)
        task_id = mediakit.enhance_video(src, resolution=cfg["enhance_resolution"], scene=cfg["enhance_scene"],
                                         tool_version=cfg["enhance_tool_version"], bitrate_level=cfg["bitrate_level"],
                                         client_token=token[:64])
        entry["enhance"] = {"task_id": task_id, "client_token": token[:64], "submitted_at": now_iso(),
                            "status": "submitted", "attempt": attempt, "source": src}
        save_state(out, state)
        log(f"[enhance] shot {n}: task {task_id}")
        to_poll.append(entry)
    if to_poll:
        _wait_mediakit(out, state, args, to_poll, key="enhance", dest=lambda e: out / "enhanced" / f"shot_{e['index']}.mp4")
    mark_done(out, state, "enhance")


def _wait_mediakit(out: Path, state: dict, args, entries: list[dict], *, key: str, dest) -> None:
    by_task = {e[key]["task_id"]: e for e in entries}

    def on_update(task_id: str, task: dict) -> None:
        e = by_task[task_id][key]
        e["status"] = str(task.get("status", "")).lower()
        if task.get("error"):
            e["error"] = task["error"]
        save_state(out, state)

    results = mediakit.wait_for_tasks(list(by_task), poll_interval=args.poll_interval,
                                      max_wait=args.mediakit_max_wait, on_update=on_update, label=key)
    failures = []
    for task_id, task in results.items():
        entry = by_task[task_id]
        write_log(out, f"mediakit_{key}_{entry.get('index', 'final')}.json", task)
        if str(task.get("status", "")).lower() == "completed":
            url = mediakit.result_url(task)
            entry[key]["video_url"] = url
            entry[key]["completed_at"] = now_iso()
            entry[key]["duration"] = task.get("duration")
            entry[key]["resolution"] = task.get("resolution")
            local = dest(entry)
            ark.download(url, local)
            entry[key]["local_path"] = str(local.relative_to(out))
            log(f"[{key}] downloaded {local.relative_to(out)} ({local.stat().st_size/1e6:.1f} MB)")
        else:
            failures.append(f"{task_id}: {task.get('status')} {task.get('error')}")
        save_state(out, state)
    if failures:
        raise SystemExit(f"[{key}] failed tasks:\n  " + "\n  ".join(failures) + "\nRe-run with --retry-failed to resubmit them.")


# ---------------------------------------------------------------------- concat


def step_concat(out: Path, state: dict, args) -> None:
    cfg = state["config"]
    cc = state["concat"]
    final = out / "final.mp4"
    if final.exists() and final.stat().st_size > 0:
        cc["local_path"] = "final.mp4"
        mark_done(out, state, "concat")
        log("[concat] final.mp4 already exists")
        return
    if cc.get("task_id") and cc.get("status") not in mediakit.FAILED_STATES:
        pass
    else:
        if cc.get("task_id") and not args.retry_failed:
            raise SystemExit(f"concat task {cc['task_id']} ended {cc['status']}: {cc.get('error')}\nRe-run with --retry-failed.")
        use_local = args.concat_from_local or args.skip_enhance or not all(e["enhance"].get("video_url") for e in state["shots"])
        inputs = [str(out / e["enhance"]["local_path"]) if use_local else e["enhance"]["video_url"] for e in state["shots"]]
        attempt = cc.get("attempt", 0) + 1
        token = f"{state['run_id']}-concat" + (f"-r{attempt}" if attempt > 1 else "")
        task_id = mediakit.concat_video(inputs, transitions=cfg["transitions"] or None, client_token=token[:64])
        state["concat"] = cc = {"task_id": task_id, "client_token": token[:64], "submitted_at": now_iso(), "status": "submitted",
                                "attempt": attempt, "inputs": "local" if use_local else "url", "input_list": inputs}
        save_state(out, state)
        log(f"[concat] task {task_id} over {len(inputs)} {'local files' if use_local else 'URLs'}")
    holder = {"concat": cc, "index": "final"}
    _wait_mediakit(out, state, args, [holder], key="concat", dest=lambda e: final)
    state["concat"] = holder["concat"]
    mark_done(out, state, "concat")


# --------------------------------------------------------------------- summary


def print_summary(out: Path, state: dict) -> None:
    print(json.dumps({
        "run_id": state["run_id"], "out": str(out), "title": state.get("title"), "steps_done": state["steps_done"],
        "final": str(out / state["concat"]["local_path"]) if state["concat"].get("local_path") else None,
        "shots": [{"index": e["index"], "duration": e["duration"], "references": e["reference_ids"],
                   "seedance_task": e["seedance"].get("task_id"), "clip": e["seedance"].get("local_path"),
                   "enhance_task": e["enhance"].get("task_id"), "enhanced": e["enhance"].get("local_path")}
                  for e in state["shots"]],
        "concat_task": state["concat"].get("task_id"),
    }, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------------ main


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mediakit_schema:
        print(json.dumps({"enhance-video": mediakit.schema("video", "enhance-video"),
                          "concat-video": mediakit.schema("editing", "concat-video")}, indent=2, ensure_ascii=False))
        return 0
    if not 4 <= args.shot_seconds <= 30:
        raise SystemExit("--shot-seconds must be within 4..30 (Seedance 2.5)")
    out = Path(args.out or Path("runs") / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug(args.idea)}")
    state = load_or_init_state(out, args)

    paid_media = not args.dry_run and args.until not in ("screenplay",)
    if paid_media:
        log(f"[pipeline] mediakit-cli {mediakit.preflight()}")  # fail before any paid Ark call

    sp = step_screenplay(out, state, args)
    if args.dry_run:
        print_dry_run(sp, state)
        return 0
    if args.until == "screenplay":
        print_summary(out, state)
        return 0

    step_characters(out, state, sp)
    if args.until == "characters":
        print_summary(out, state); return 0
    step_shots(out, state, sp, args)
    if args.until == "shots":
        print_summary(out, state); return 0
    step_enhance(out, state, args)
    if args.until == "enhance":
        print_summary(out, state); return 0
    step_concat(out, state, args)
    print_summary(out, state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ark.ArkError, mediakit.MediaKitError) as exc:
        log(f"[pipeline] ERROR: {exc}")
        sys.exit(1)
