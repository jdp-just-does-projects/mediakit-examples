# mediakit-examples

Runnable examples for **AI MediaKit** — ByteDance's cloud media-processing service, offered on
Volcano Engine (mainland China) and on BytePlus (international) — used as the post-production
stage behind ByteDance's generative models (Seedance, Seedream). Each example is self-contained,
with its own README and `requirements.txt`.

## What is MediaKit?

[AI MediaKit](https://www.volcengine.com/product/ai-mediakit) exposes cloud tools such as video
quality enhancement / super-resolution, generative restoration, subtitle erasing, ASR, OCR,
scene segmentation, matting, and editing primitives (trim, concat, mux, image-to-video). Its
official surface is the **`mediakit-cli`** command-line tool
([github.com/volcengine/mediakit-cli](https://github.com/volcengine/mediakit-cli), MIT):

```
npx @volcengine/mediakit-cli install -y
export MEDIAKIT_API_KEY=...        # from the AI MediaKit console of the cloud you use
mediakit-cli --cloud video enhance-video --video-url https://.../in.mp4 --resolution 1080p
mediakit-cli shared query-task --task-id <id> --poll-complete
```

Cloud tools are asynchronous (submit → `task_id` → `query-task`); results come back as URLs.
A subset of editing tools can also run locally on ffmpeg (`--local`), with no key. The same
CLI talks to either cloud: its default endpoint is Volcano Engine, and `MEDIAKIT_ENDPOINT`
points it at BytePlus.

## Two clouds, one CLI, different tool sets

ByteDance runs two separate cloud businesses — **Volcano Engine (火山引擎)** for mainland China
and **BytePlus** internationally — and AI MediaKit exists on both, with the same API shape
(`POST /api/v1/tools/<tool>`, `GET /api/v1/tasks/{id}`, `Authorization: Bearer <key>`) but
different endpoints, consoles, keys and — importantly — different enabled tools:

| | Volcano Engine (火山引擎) | BytePlus |
| --- | --- | --- |
| MediaKit endpoint | `https://amk.cn-beijing.volces.com` (the CLI default) | `https://mediakit.ap-southeast-1.bytepluses.com` (`MEDIAKIT_ENDPOINT`) |
| MediaKit key | [console.volcengine.com/imp/ai-mediakit/settings](https://console.volcengine.com/imp/ai-mediakit/settings) | [BytePlus VOD console → AI MediaKit → Settings → API key](https://console.byteplus.com/vodpaas/region:vodpaas+ap-southeast-1/ai-mediakit/settings?tab=apiKey) |
| `video enhance-video` | Yes | Yes |
| `editing concat-video` (cloud stitch) | Yes | **No** — `AccessDenied: tool concat-video is not available`; stitch locally with `mediakit-cli --local` (ffmpeg) |
| Local file → cloud tool (CLI upload) | Yes (`mediakit://` ids, cached 30 d) | **No** — `tool request-media-upload-url is not available`; inputs must be public HTTPS URLs that answer `HEAD` |
| Docs | [volcengine.com/docs](https://www.volcengine.com/product/ai-mediakit) | [docs.byteplus.com → BytePlus VOD → AI MediaKit](https://docs.byteplus.com/en/docs/byteplus-vod/ai-mediakit-video-quality-enhancement) |
| ModelArk (Seedance / Seedream / LLMs) | `https://ark.cn-beijing.volces.com/api/v3`, `doubao-*` model ids | `https://ark.ap-southeast.bytepluses.com/api/v3`, `dreamina-*` / `dola-*` model ids |

Keys do not cross clouds, so each example is one cloud end to end: one account, one console,
two keys (`ARK_API_KEY` for ModelArk, `MEDIAKIT_API_KEY` for MediaKit). The BytePlus tool
list above is what the service answered on 2026-08-26, not a doc claim; the BytePlus docs only
list enhancement, erasure, blurring, frame extraction, highlight clipping, scene segmentation,
transcoding, remuxing, trim, audio concat/extract/mix/merge, speed, volume/fade and voice
separation.

## Repository layout

Examples are nested `platform/surface/usage`:

```
volcengine/
  mediakit-cli/
    seedance-short-film/   # LLM screenplay -> Seedream characters -> Seedance 2.5 480p clips
                           # -> MediaKit enhance-video 1080p -> MediaKit concat-video
byteplus/
  mediakit-cli/
    seedance-short-film/   # same film on BytePlus: enhance-video from the Seedance URLs,
                           # stitched locally (BytePlus has no concat-video / upload tool)
```

- **platform** — `volcengine` or `byteplus`.
- **surface** — how MediaKit is driven: `mediakit-cli` today; SDK / MCP later.
- **usage** — the workflow the example demonstrates.

## Examples

| Example | What it shows | Verified live |
| --- | --- | --- |
| [`volcengine/mediakit-cli/seedance-short-film`](volcengine/mediakit-cli/seedance-short-film) | A coherent ≥60 s short film: an LLM writes a 4-shot screenplay, Seedream renders consistent character sheets, Seedance 2.5 generates 4 × 24 s clips at 480p from them, MediaKit upscales each clip to 1080p and stitches them. Resumable, with every request body inspectable via `--dry-run`. | Yes — end to end on Volcano Engine (2026-08-25/26): LLM, Seedream, 4 × 24 s Seedance 2.5 clips, MediaKit enhance to 1080p, MediaKit cloud concat; audio preserved throughout. Figures in its README. |
| [`byteplus/mediakit-cli/seedance-short-film`](byteplus/mediakit-cli/seedance-short-film) | The same pipeline on BytePlus: BytePlus ModelArk (`dreamina-seedance-2-5`, `dola-seedream-5-0-pro`) for the film, BytePlus AI MediaKit `enhance-video` fed the Seedance clip URLs directly, and `mediakit-cli --local` (ffmpeg) for the stitch. | Yes — end to end on BytePlus (2026-08-26): LLM, Seedream, 4 × 24 s Seedance 2.5 clips, MediaKit enhance to 1080p from URLs, local concat; audio preserved throughout. Figures in its README. |

## Conventions

Every example directory is standalone:

- `README.md` — what it does, how to run it, exactly what it sends, its sharp edges, and what
  was verified against the live services versus taken from the docs.
- `requirements.txt` — install with `pip install -r requirements.txt`. `mediakit-cli` itself
  is a Node package installed separately.
- Credentials and endpoints come from environment variables — `ARK_API_KEY`,
  `MEDIAKIT_API_KEY`, and whatever else that example needs. Nothing is hardcoded, and no keys
  are committed. Generated media goes to `runs/`, which is git-ignored.

## License

[MIT](LICENSE).
