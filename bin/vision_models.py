from __future__ import annotations

from typing import Optional

VISION_NAME_MARKERS: tuple[str, ...] = (
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "qwen2-vl",
    "qwen2.5-vl",
    "llama3.2-vision",
    "gemma3",
    "granite-vision",
    "vision",  # broad tag fallback; keep last
)


def model_supports_vision(model: Optional[str]) -> bool:
    name = (model or "").strip().lower()
    if not name:
        return False
    return any(m in name for m in VISION_NAME_MARKERS)


def normalize_image_b64(value: str) -> str:
    s = (value or "").strip()
    if "base64," in s:
        s = s.split("base64,", 1)[1].strip()
    return s
