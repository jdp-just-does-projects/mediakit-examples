# mediakit-examples

Runnable examples for **AI MediaKit** — Volcano Engine's cloud media-processing service — used
as the post-production stage behind ByteDance's generative models (Seedance, Seedream). Each
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
also run locally on ffmpeg (`--local`), with no key.

## Volcano Engine only

ByteDance runs two separate cloud businesses — **Volcano Engine (火山引擎)** for mainland China
and **BytePlus** internationally — and MediaKit exists only on the first:

| | Volcano Engine (火山引擎) | BytePlus |
| --- | --- | --- |
| AI MediaKit (API key + `mediakit-cli`) | Yes — `https://amk.cn-beijing.volces.com` | **No.** Not in the console; `mediakit-cli` has no BytePlus endpoint. |
| Closest equivalent | — | Video enhancement inside [BytePlus VOD (vCube)](https://docs.byteplus.com/en/docs/byteplus-vod/docs-video-enhancement): a VOD space, an enhancement template and an AK/SK-signed OpenAPI — a different integration, not a config switch. |
| ModelArk (Seedance / Seedream / LLMs) | `https://ark.cn-beijing.volces.com/api/v3`, `doubao-*` model ids | `https://ark.ap-southeast.bytepluses.com/api/v3`, `dreamina-*` / `dola-*` model ids |

Because the MediaKit half has to be Volcano Engine, every example here is Volcano Engine
end to end — one account, one console, two keys (`ARK_API_KEY` for ModelArk,
`MEDIAKIT_API_KEY` for MediaKit). BytePlus ModelArk speaks the same Ark API, and earlier
revisions of this repo supported it behind an `ARK_PLATFORM` switch; that branch was removed
to keep the examples honest about what runs where. If MediaKit ships on BytePlus, a
`byteplus/` tree will come back.

## Repository layout

Examples are nested `platform/surface/usage`:

```
volcengine/
  mediakit-cli/
    seedance-short-film/   # LLM screenplay -> Seedream characters -> Seedance 2.5 480p clips
                           # -> MediaKit enhance-video 1080p -> MediaKit concat-video
```

- **platform** — `volcengine` (the only one MediaKit runs on today).
- **surface** — how MediaKit is driven: `mediakit-cli` today; SDK / MCP later.
- **usage** — the workflow the example demonstrates.

## Examples

| Example | What it shows | Verified live |
| --- | --- | --- |
| [`volcengine/mediakit-cli/seedance-short-film`](volcengine/mediakit-cli/seedance-short-film) | A coherent ≥60 s short film: an LLM writes a 4-shot screenplay, Seedream renders consistent character sheets, Seedance 2.5 generates 4 × 24 s clips at 480p from them, MediaKit upscales each clip to 1080p and stitches them. Resumable, with every request body inspectable via `--dry-run`. | Ark half yes (2026-08-25: LLM, Seedream, 4 × 24 s Seedance 2.5 clips, local concat); MediaKit cloud steps pending a key — see its README |

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
