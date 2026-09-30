"""Talk to a local model server on this computer.

Resume text stays here. This module never calls a remote LLM API.
Ollama on Apple Silicon runs the model on the GPU (Metal) when it is loaded.
"""

from __future__ import annotations

import json
import platform
import time
import urllib.error
import urllib.request

HOST = "127.0.0.1"
PORT = 11434
BASE = f"http://{HOST}:{PORT}"

# Prefer a model already installed that follows short JSON instructions.
# qwen2.5vl:3b is on this Mac, fits in GPU memory, and answered a structured prompt.
PREFER = (
    "qwen2.5vl:3b",
    "qwen2.5:7b-instruct",
    "qwen2.5:7b",
    "qwen2.5:3b",
    "qwen2.5vl:7b",
    "llama3.2:3b",
    "llama3.1:8b",
    "llama3.2:1b",
)

_CACHE = {"at": 0.0, "status": None}


class LocalModelError(Exception):
    pass


def _request(path, payload=None, timeout=8):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise LocalModelError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise LocalModelError("not reachable") from exc
    except TimeoutError as exc:
        raise LocalModelError("timed out") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LocalModelError("not JSON") from exc


def _choose(models):
    names = []
    for item in models or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
    for pref in PREFER:
        for name in names:
            if name == pref or name.startswith(pref + "-"):
                return name
    for name in names:
        low = name.lower()
        if low.startswith("moondream") or low.startswith("llava"):
            continue
        return name
    return ""


def _gpu_loaded(model):
    try:
        data = _request("/api/ps", timeout=3)
    except LocalModelError:
        return None
    for item in data.get("models") or []:
        if not isinstance(item, dict):
            continue
        if item.get("name") != model and item.get("model") != model:
            continue
        vram = int(item.get("size_vram") or 0)
        if vram > 0:
            return True
        return False
    return None


def status(force=False):
    now = time.time()
    cached = _CACHE.get("status")
    if not force and cached and now - float(_CACHE.get("at") or 0) < 12:
        return dict(cached)
    payload = {
        "available": False,
        "backend": "",
        "model": "",
        "gpu": False,
        "label": "Built-in matcher (no local model is responding)",
    }
    try:
        data = _request("/api/tags", timeout=3)
    except LocalModelError:
        _CACHE["at"] = now
        _CACHE["status"] = payload
        return dict(payload)
    model = _choose(data.get("models") or [])
    if not model:
        payload["label"] = "Built-in matcher (Ollama is up, but no text model is installed)"
        _CACHE["at"] = now
        _CACHE["status"] = payload
        return dict(payload)
    gpu = _gpu_loaded(model)
    apple = platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}
    on_gpu = gpu is True or (gpu is None and apple)
    payload = {
        "available": True,
        "backend": "ollama",
        "model": model,
        "gpu": bool(on_gpu),
        "label": (
            f"{model} via Ollama on this Mac's GPU"
            if on_gpu
            else f"{model} via Ollama on this Mac"
        ),
    }
    _CACHE["at"] = now
    _CACHE["status"] = payload
    return dict(payload)


def extract_json(text):
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def complete(system, user, timeout=40):
    info = status()
    if not info.get("available") or not info.get("model"):
        return None
    payload = {
        "model": info["model"],
        "stream": False,
        "keep_alive": "20m",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {
            "temperature": 0.1,
            "num_predict": 420,
            "num_gpu": 999,
            "num_ctx": 4096,
        },
    }
    try:
        data = _request("/api/chat", payload, timeout=timeout)
    except LocalModelError:
        return None
    content = ((data.get("message") or {}).get("content") or "").strip()
    parsed = extract_json(content)
    if not parsed:
        return None
    # Refresh GPU flag now that the model has been asked to load.
    _CACHE["at"] = 0
    return parsed


def warmup():
    info = status(force=True)
    if not info.get("available"):
        return info
    complete("Reply with JSON only.", 'Return {"ok": true}.', timeout=50)
    return status(force=True)
