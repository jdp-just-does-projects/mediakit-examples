"""Endpoints and model ids. Everything here is BytePlus (ByteDance's international cloud).

AI MediaKit on BytePlus lives inside BytePlus VOD, in the ap-southeast-1
(Johor) region, with its own API key (MEDIAKIT_API_KEY, from the AI MediaKit
console settings page). It speaks the same API as the Volcano Engine MediaKit
(`POST /api/v1/tools/<tool>`, `GET /api/v1/tasks/{id}`, `Authorization:
Bearer`), so the official `mediakit-cli` works unchanged once MEDIAKIT_ENDPOINT
points at the BytePlus host — which mediakit.py sets by default.

What differs from the Volcano Engine deployment (verified live 2026-08-26):
  - only a subset of tools is enabled: video enhance-video yes;
    editing concat-video and the CLI's local-file upload
    (request-media-upload-url) answer `AccessDenied: tool ... is not available`.
    So enhance-video must be given a public URL, and stitching is done locally.
  - the docs' quickstart sample URL (vod-ai-test.byteplus.com) does not resolve;
    the guide's vod-ai-test.bytepluses.com host does.

ModelArk on BytePlus is the ap-southeast deployment with the dreamina-* / dola-*
model ids (the doubao-* ids are Volcano Engine only).
"""

ARK_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
VIDEO_MODEL = "dreamina-seedance-2-5-260628"     # Seedance 2.5
IMAGE_MODEL = "dola-seedream-5-0-pro-260628"     # Seedream 5.0 Pro
LLM_MODEL = "deepseek-v4-pro-260425"             # any chat model works; ARK_LLM_MODEL overrides

MEDIAKIT = {
    # Overridable with MEDIAKIT_ENDPOINT. mediakit-cli's own default is the Volcano
    # Engine host (amk.cn-beijing.volces.com); mediakit.py exports this one instead.
    "endpoint": "https://mediakit.ap-southeast-1.bytepluses.com",
    "console": "https://console.byteplus.com/vodpaas/region:vodpaas+ap-southeast-1/ai-mediakit/settings?tab=apiKey",
    "docs": "https://docs.byteplus.com/en/docs/byteplus-vod/ai-mediakit-video-quality-enhancement",
}
