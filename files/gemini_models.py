from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PREFERRED_GEMINI_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
)


def normalize_model_name(model_name: str) -> str:
    if not model_name:
        return ""
    normalized = str(model_name).strip()
    if normalized.startswith("models/"):
        normalized = normalized.split("/", 1)[1]
    return normalized


def list_generate_content_models(client) -> list[str]:
    response = client.models.list()
    models = getattr(response, "models", response)
    available = []

    for model in models:
        supported_actions = getattr(model, "supported_actions", None) or []
        if "generateContent" not in supported_actions:
            continue
        model_name = normalize_model_name(getattr(model, "name", "") or getattr(model, "id", ""))
        if model_name:
            available.append(model_name)

    return available


def select_generate_content_model(client, requested_model: str | None) -> tuple[str, list[str], bool]:
    requested_model = normalize_model_name(requested_model)
    available_models = list_generate_content_models(client)

    candidates = []
    if requested_model:
        candidates.append(requested_model)
    for model_name in PREFERRED_GEMINI_MODELS:
        if model_name not in candidates:
            candidates.append(model_name)

    if available_models:
        for candidate in candidates:
            if candidate in available_models:
                return candidate, available_models, candidate != requested_model

        raise RuntimeError(
            "No supported Gemini generateContent model is available. "
            f"Requested: {requested_model or 'unset'}. "
            f"Available: {', '.join(sorted(available_models))}"
        )

    fallback_model = requested_model or PREFERRED_GEMINI_MODELS[0]
    return fallback_model, available_models, False


def is_model_not_found_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "not found" in message and "model" in message
