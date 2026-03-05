"""Baseline serialization helpers for JSON config persistence."""

from __future__ import annotations

import base64

import numpy as np


def encode_baselines(baselines: dict[int, np.ndarray]) -> list[dict]:
    """Encode baselines for JSON with explicit slot indices (backward compatible)."""
    return [
        {
            "slot_index": int(i),
            "shape": list(ary.shape),
            "data": base64.b64encode(ary.tobytes()).decode(),
        }
        for i in sorted(baselines.keys())
        for ary in [baselines[i]]
    ]


def decode_baselines(data: list[dict]) -> dict[int, np.ndarray]:
    """Decode baselines from config (supports legacy entries without slot_index)."""
    result = {}
    for i, d in enumerate(data):
        if not isinstance(d, dict):
            continue
        shape = d.get("shape")
        b64 = d.get("data")
        slot_index = d.get("slot_index", i)
        if shape and b64:
            arr = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
            result[int(slot_index)] = arr.reshape(shape).copy()
    return result


def encode_baselines_by_form(baselines_by_form: dict[str, dict[int, np.ndarray]]) -> dict:
    encoded: dict[str, list[dict]] = {}
    for form_id, baselines in dict(baselines_by_form or {}).items():
        fid = str(form_id or "").strip().lower()
        if not fid:
            continue
        encoded[fid] = encode_baselines(baselines)
    return encoded


def decode_baselines_by_form(data: object) -> dict[str, dict[int, np.ndarray]]:
    if not isinstance(data, dict):
        return {}
    decoded: dict[str, dict[int, np.ndarray]] = {}
    for form_id, encoded in data.items():
        fid = str(form_id or "").strip().lower()
        if not fid or not isinstance(encoded, list):
            continue
        decoded[fid] = decode_baselines(encoded)
    return decoded


def encode_gray_template(gray: np.ndarray) -> dict:
    return {
        "shape": [int(gray.shape[0]), int(gray.shape[1])],
        "data": base64.b64encode(gray.astype(np.uint8).tobytes()).decode(),
    }


def encode_color_template(bgr: np.ndarray) -> dict:
    return {
        "shape": [int(bgr.shape[0]), int(bgr.shape[1]), int(bgr.shape[2])],
        "data": base64.b64encode(bgr.astype(np.uint8).tobytes()).decode(),
    }
