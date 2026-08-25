"""Per-platform Ark values, plus the (Volcengine-only) MediaKit endpoint.

Seedance, Seedream and the chat models ship on two separate Ark deployments.
They speak the same API, but the base URL, the model ids, and the API key are
all per-platform, and none of them carries over: each platform returns
InvalidEndpointOrModel.NotFound for the other's model id, and 401 for the
other's key. Select with ARK_PLATFORM.

MediaKit (the upscale + stitch half of this example) is a Volcengine product
with a single endpoint, so it has no platform switch. It uses its own key,
MEDIAKIT_API_KEY, from https://console.volcengine.com/imp/ai-mediakit/settings.
"""

PLATFORMS = {
    "byteplus": {
        "base_url": "https://ark.ap-southeast.bytepluses.com/api/v3",
        "video_model": "dreamina-seedance-2-5-260628",   # Seedance 2.5
        "image_model": "dola-seedream-5-0-pro-260628",   # Seedream 5.0 Pro
        # Any chat model on the platform works; DeepSeek V4 Pro is available on
        # both sides. seed-2-0-lite-260228 is a cheaper alternative here.
        "llm_model": "deepseek-v4-pro-260813",
    },
    "volcengine": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "video_model": "doubao-seedance-2-5-260628",
        "image_model": "doubao-seedream-5-0-pro-260628",
        # doubao-seed-2-1-pro-260628 is the in-house alternative here.
        "llm_model": "deepseek-v4-pro-260425",
    },
}

MEDIAKIT = {
    # Overridable with MEDIAKIT_ENDPOINT; this is also mediakit-cli's own default.
    "endpoint": "https://amk.cn-beijing.volces.com",
    "console": "https://console.volcengine.com/imp/ai-mediakit/settings",
}
