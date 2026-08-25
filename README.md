# mediakit-examples

Runnable examples for **AI MediaKit** — Volcengine's cloud media-processing service — used as
the post-production stage behind ByteDance's generative models (Seedance, Seedream). Each
example is self-contained, with its own README and `requirements.txt`.

## What is MediaKit?

[AI MediaKit](https://www.volcengine.com/product/ai-mediakit) exposes cloud tools such as video
quality enhancement / super-resolution, generative restoration, subtitle erasing, ASR, OCR,
scene segmentation, matting, and editing primitives (trim, concat, mux, image-to-video). Its
official surface is the **`mediakit-cli`** command-line tool
([github.com/volcengine/mediakit-cli](https://github.com/volcengine/mediakit-cli), MIT):

```
npx @volcengine/mediakit-cli install -y
export MEDIAKIT_API_KEY=...        # https://console.volcengine.com/imp/ai-mediakit/settings
mediakit-cli --cloud video enhance-video --video-url in.mp4 --resolution 1080p
mediakit-cli shared query-task --task-id <id> --poll-complete
```

Cloud tools are asynchronous (submit → `task_id` → `query-task`); results come back as URLs.
Local files passed to a cloud tool are uploaded by the CLI. A subset of editing tools can
also run locally on ffmpeg (`--local`).

## Which platform am I on?

| | Volcano Engine (火山引擎) | BytePlus |
| --- | --- | --- |
| Market | Mainland China | International |
| MediaKit endpoint | `https://amk.cn-beijing.volces.com` | — (no MediaKit deployment today) |
| ModelArk base URL | `https://ark.cn-beijing.volces.com/api/v3` | `https://ark.ap-southeast.bytepluses.com/api/v3` |
| Seedance 2.5 model id | `doubao-seedance-2-5-260628` | `dreamina-seedance-2-5-260628` |
| Seedream 5.0 Pro model id | `doubao-seedream-5-0-pro-260628` | `dola-seedream-5-0-pro-260628` |

MediaKit is Volcengine-only, so every example lives under `volcengine/`. The generative half of
an example (Ark: Seedance, Seedream, chat models) can still target either platform via
`ARK_PLATFORM` — base URL, model ids and API key are per-platform and not interchangeable.

## Repository layout

Examples are nested `platform/surface/usage`:

```
volcengine/
  mediakit-cli/
    seedance-short-film/   # LLM screenplay -> Seedream characters -> Seedance 2.5 480p clips
                           # -> MediaKit enhance-video 1080p -> MediaKit concat-video
```

- **platform** — `volcengine` (or `byteplus`, when MediaKit ships there).
- **surface** — how MediaKit is driven: `mediakit-cli` today; SDK / MCP later.
- **usage** — the workflow the example demonstrates.

## Examples

| Example | What it shows | Verified live |
| --- | --- | --- |
| [`volcengine/mediakit-cli/seedance-short-film`](volcengine/mediakit-cli/seedance-short-film) | A coherent ≥60 s short film: an LLM writes a 4-shot screenplay, Seedream renders consistent character sheets, Seedance 2.5 generates 4 × 24 s clips at 480p from them, MediaKit upscales each clip to 1080p and stitches them. Resumable, with every request body inspectable via `--dry-run`. | Ark half yes (BytePlus, 2026-08-25: LLM, Seedream, 4 × 24 s Seedance 2.5 clips); MediaKit steps pending a key — see its README |

## Conventions

Every example directory is standalone:

- `README.md` — what it does, how to run it, exactly what it sends, its sharp edges, and what
  was verified against the live services versus taken from the docs.
- `requirements.txt` — install with `pip install -r requirements.txt`. `mediakit-cli` itself
  is a Node package installed separately.
- Credentials and endpoints come from environment variables — `MEDIAKIT_API_KEY`,
  `ARK_PLATFORM`, `ARK_API_KEY`, and whatever else that example needs. Nothing is hardcoded,
  and no keys are committed. Generated media goes to `runs/`, which is git-ignored.

## License

[MIT](LICENSE).
