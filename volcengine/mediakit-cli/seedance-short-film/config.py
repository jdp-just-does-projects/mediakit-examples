"""Endpoints and model ids. Everything here is Volcano Engine (火山引擎).

AI MediaKit — the upscale + stitch half of this example — is a Volcano Engine
product with a single endpoint and its own key (MEDIAKIT_API_KEY, from
https://console.volcengine.com/imp/ai-mediakit/settings). It is not offered on
BytePlus, so the whole example targets Volcano Engine and the Ark half uses the
cn-beijing ModelArk deployment with the doubao-* model ids.

(BytePlus ModelArk speaks the same Ark API with different model ids —
dreamina-seedance-2-5-260628, dola-seedream-5-0-pro-260628 — but there is no
MediaKit endpoint to pair it with, so it is deliberately not wired in here.)
"""

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
VIDEO_MODEL = "doubao-seedance-2-5-260628"       # Seedance 2.5
IMAGE_MODEL = "doubao-seedream-5-0-pro-260628"   # Seedream 5.0 Pro
LLM_MODEL = "deepseek-v4-pro-260425"             # any chat model works; ARK_LLM_MODEL overrides

MEDIAKIT = {
    # Overridable with MEDIAKIT_ENDPOINT; this is also mediakit-cli's own default.
    "endpoint": "https://amk.cn-beijing.volces.com",
    "console": "https://console.volcengine.com/imp/ai-mediakit/settings",
}
