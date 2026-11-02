#!/usr/bin/env python3
"""scripts/patch_catalog_1m.py — Ensure 1M context window for frontier models in 9Router.

9Router's hardcoded PATTERN_CAPABILITIES (*step-*) forces Step-5 models to 128k,
and unknown models like Inception mercury-3 fall back to 200k.
When 9Router syncs its catalog from upstream, it overwrites ~/.9router/model-catalog.json
and wipes manual provider overrides.

This script injects the true 1M (1,000,000) contextWindow and 64k maxOutput
into ~/.9router/model-catalog.json. 9Router hot-reloads it immediately on next request.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CATALOG_PATH = Path.home() / ".9router/model-catalog.json"

PROVIDERS_1M = {
    # Nararouter (nr)
    "nr": ["step-5-preview", "step-5-preview-free"],
    "Nararouter": ["step-5-preview", "step-5-preview-free"],
    "openai-compatible-chat-900c78bf-2fb5-41c1-a0bd-9fc4a8df6fdc": ["step-5-preview", "step-5-preview-free"],

    # Cline / cl
    "cl": ["step-5-preview", "stepfun/step-5-preview", "inception/mercury-3", "mercury-3"],
    "cline": ["step-5-preview", "stepfun/step-5-preview", "inception/mercury-3", "mercury-3"],

    # CodeBuddy / cbai
    "cbai": ["step-5-preview"],
    "codebuddy": ["step-5-preview"],
    "codebuddy-intl": ["step-5-preview"],

    # OpenCode / oc
    "oc": ["step-5-preview", "step-5-preview-free"],
    "opencode": ["step-5-preview", "step-5-preview-free"],
    "opencode-go": ["step-5-preview", "step-5-preview-free"],

    # Zenmux
    "zenmux": ["step-5-preview", "stepfun/step-5-preview"],
    "anthropic-compatible-0d027b8c-5a12-4645-9f6b-80b9c8f48c64": ["step-5-preview", "stepfun/step-5-preview"],

    # Inception Labs
    "Inception": ["mercury-3", "mercury-2.5", "mercury-2"],
    "inception": ["mercury-3", "mercury-2.5", "mercury-2"],
    "openai-compatible-chat-7e5c78c4-17c5-4dee-850b-e883f151de46": ["mercury-3", "mercury-2.5", "mercury-2"],
    "xperimential": ["mercury-3", "mercury-2.5", "mercury-2"],
    "openai-compatible-chat-2d4406b4-fa14-4e91-8a81-261993017605": ["mercury-3", "mercury-2.5", "mercury-2"],

    # Freebuff
    "Freebuff": ["freebuff-agent", "default"],
    "openai-compatible-chat-freebuff": ["freebuff-agent", "default"],
}


def patch_catalog() -> None:
    if not CATALOG_PATH.exists():
        print(f"Catalog not found at {CATALOG_PATH}")
        return

    try:
        data = json.loads(CATALOG_PATH.read_text("utf-8"))
    except Exception as e:
        print(f"Failed to read catalog: {e}")
        return

    providers = data.setdefault("providers", {})
    count = 0

    for prov, models in PROVIDERS_1M.items():
        p_dict = providers.setdefault(prov, {})
        for mod in models:
            p_dict[mod] = {"contextWindow": 1000000, "maxOutput": 64000}
            count += 1

    tmp = CATALOG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    os.replace(tmp, CATALOG_PATH)
    print(f"Successfully patched {count} model entries to 1M context in {CATALOG_PATH}")


if __name__ == "__main__":
    patch_catalog()
