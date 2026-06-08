"""
JuhRadial MX - AI Prompt Builder configuration

Loads and persists the ``ai`` section of ``~/.config/juhradial/config.json``,
modelled after Logitech Options+ "Logi AI Prompt Builder". Two execution
backends are supported:

  - "cli"       : drive an installed agent CLI (default: Claude Code ``claude -p``).
                  No API key required - reuses the user's existing login.
  - "anthropic" : Anthropic Messages API (requires an API key).
  - "openai"    : OpenAI Chat Completions API (requires an API key).

SPDX-License-Identifier: GPL-3.0
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "juhradial" / "config.json"

# Recipes mirror the quick actions offered by Logi AI Prompt Builder.
# Each recipe is an instruction applied to the selected text. ``{text}`` is
# substituted with the selection; if absent, the selection is appended.
DEFAULT_RECIPES = [
    {"id": "rephrase", "label": "Rephrase", "icon": "rephrase",
     "prompt": "Rephrase the following text so it reads more clearly and naturally, keeping the same meaning and language."},
    {"id": "improve", "label": "Improve writing", "icon": "improve",
     "prompt": "Improve the writing of the following text: fix awkward phrasing and word choice while keeping the same meaning and language."},
    {"id": "shorter", "label": "Make shorter", "icon": "shorter",
     "prompt": "Make the following text more concise while keeping its meaning and language."},
    {"id": "longer", "label": "Make longer", "icon": "longer",
     "prompt": "Expand the following text with more detail while keeping its meaning and language."},
    {"id": "professional", "label": "Professional tone", "icon": "professional",
     "prompt": "Rewrite the following text in a professional tone, keeping its meaning and language."},
    {"id": "friendly", "label": "Friendly tone", "icon": "friendly",
     "prompt": "Rewrite the following text in a warm, friendly tone, keeping its meaning and language."},
    {"id": "grammar", "label": "Fix spelling & grammar", "icon": "grammar",
     "prompt": "Correct the spelling and grammar of the following text. Keep the original language and wording as much as possible."},
    {"id": "summarize", "label": "Summarize", "icon": "summarize",
     "prompt": "Summarize the key points of the following text in its original language."},
    {"id": "translate_en", "label": "Translate to English", "icon": "translate",
     "prompt": "Translate the following text to English."},
    {"id": "reply", "label": "Write a reply", "icon": "reply",
     "prompt": "Write a suitable reply to the following message, in the same language."},
]

DEFAULT_AI_CONFIG = {
    # "cli" | "anthropic" | "openai"
    "backend": "cli",
    # Output language: "auto" (keep input language), "en", or "pt-BR".
    "output_language": "auto",
    # Default engine the Prompt Builder opens with: "claude" / "chatgpt" / "gemini".
    "default_engine": "claude",
    # Selected model variant per engine (see ENGINE_MODELS for the options).
    "engine_model": {"claude": "sonnet", "chatgpt": "gpt-4o", "gemini": "gemini-2.5-flash"},
    # Restore the user's clipboard after capturing the selection / pasting.
    "preserve_clipboard": True,
    # Delay (ms) between simulating Ctrl+C and reading the clipboard.
    "capture_delay_ms": 120,
    # CLI backend: a command template. {prompt} = instruction, {model} = model.
    # The selected text is piped to the command's stdin. ``provider`` selects a
    # known preset ("claude" / "gemini") or "custom" for a hand-edited command.
    "cli": {
        "provider": "claude",
        "command": ["claude", "-p", "{prompt}", "--allowedTools", "", "--model", "{model}"],
        "model": "sonnet",
        "timeout_s": 60,
    },
    "anthropic": {
        "api_key": "",                      # if empty, falls back to api_key_env
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com",
        "max_tokens": 1024,
        "timeout_s": 60,
    },
    "openai": {
        "api_key": "",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "max_tokens": 1024,
        "timeout_s": 60,
    },
    "recipes": DEFAULT_RECIPES,
}

# Shared system prompt that constrains every backend to a clean text transform.
SYSTEM_PROMPT_BASE = (
    "You are a writing assistant embedded in a desktop tool. Apply the requested "
    "transformation to the user's text and output ONLY the resulting text - no "
    "preamble, no explanation, no quotation marks, no markdown fences."
)

# Output-language clauses appended to the base prompt.
_LANG_CLAUSE = {
    "auto": " Preserve the original language unless explicitly asked to translate.",
    "en": " Always write your output in English, regardless of the input language.",
    "pt-br": " Always write your output in Brazilian Portuguese (português do Brasil), "
             "regardless of the input language.",
}

# Backwards-compatible default (auto = preserve original language).
SYSTEM_PROMPT = SYSTEM_PROMPT_BASE + _LANG_CLAUSE["auto"]

# Human labels for the output-language selector.
OUTPUT_LANGUAGES = [
    ("auto", "Auto (keep language)"),
    ("en", "English"),
    ("pt-BR", "Português (BR)"),
]

# Known CLI presets. {prompt} = instruction, {model} = model; selection on stdin.
CLI_PRESETS = {
    "claude": {
        "label": "Claude Code",
        "command": ["claude", "-p", "{prompt}", "--allowedTools", "", "--model", "{model}"],
        "model": "sonnet",
    },
    "gemini": {
        "label": "Gemini CLI",
        "command": ["gemini", "-p", "{prompt}", "-m", "{model}"],
        "model": "gemini-2.5-flash",
    },
}

# Provider dropdown order for the CLI backend (last entry = free-form custom).
CLI_PROVIDERS = [("claude", "Claude Code"), ("gemini", "Gemini CLI"), ("custom", "Custom")]

# Named engines selectable from the AI ring submenu. Each maps to a backend:
# Claude/Gemini use their CLI (no API key); ChatGPT uses the OpenAI API.
ENGINES = [("claude", "Claude"), ("chatgpt", "ChatGPT"), ("gemini", "Gemini")]

# Per-engine model variants offered as a toggle inside the builder.
# Each entry is (model_value, short_label). The first item is the default.
ENGINE_MODELS = {
    "claude": [("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")],
    "chatgpt": [("gpt-4o", "GPT-4o"), ("gpt-4o-mini", "4o-mini"), ("o4-mini", "o4-mini")],
    "gemini": [
        ("gemini-2.5-pro", "2.5 Pro"),
        ("gemini-2.5-flash", "2.5 Flash"),
        ("gemini-2.5-flash-lite", "Flash-Lite"),
    ],
}


def engine_model_options(ai_cfg: dict, engine: str):
    """Return [(value, label), ...] of model variants for an engine.

    A config override at ai.engine_models[engine] (list of strings or
    [value, label] pairs) takes precedence over the built-in defaults.
    """
    override = (ai_cfg.get("engine_models") or {}).get(engine)
    if override:
        out = []
        for it in override:
            if isinstance(it, (list, tuple)) and len(it) >= 2:
                out.append((it[0], it[1]))
            else:
                out.append((it, str(it)))
        return out
    return list(ENGINE_MODELS.get(engine, []))


def current_engine_model(ai_cfg: dict, engine: str) -> str:
    """The selected model for an engine (ai.engine_model[engine]) or its default."""
    sel = (ai_cfg.get("engine_model") or {}).get(engine)
    if sel:
        return sel
    opts = engine_model_options(ai_cfg, engine)
    return opts[0][0] if opts else ""


def apply_engine(ai_cfg: dict, engine) -> dict:
    """Return a copy of ``ai_cfg`` forced to a named engine.

    "claude"/"gemini" -> CLI backend with that tool's preset; "chatgpt" ->
    OpenAI API. An empty/unknown engine leaves the configured backend untouched.
    """
    if not engine:
        return ai_cfg
    import copy

    cfg = copy.deepcopy(ai_cfg)
    e = str(engine).lower()
    chosen_model = current_engine_model(cfg, e)
    if e in ("claude", "gemini"):
        cli = dict(cfg.get("cli", {}))
        # Use the tool's preset command unless the user already customised it for
        # this engine; the model comes from the per-engine selection.
        if cli.get("provider") != e:
            cli["command"] = list(CLI_PRESETS[e]["command"])
            cli["provider"] = e
        cli["model"] = chosen_model or CLI_PRESETS[e]["model"]
        cfg["cli"] = cli
        cfg["backend"] = "cli"
    elif e in ("chatgpt", "openai"):
        cfg["backend"] = "openai"
        openai = dict(cfg.get("openai", {}))
        if chosen_model:
            openai["model"] = chosen_model
        cfg["openai"] = openai
    elif e == "anthropic":
        cfg["backend"] = "anthropic"
    return cfg


def effective_system_prompt(ai_cfg: dict) -> str:
    """Return the system prompt with the configured output-language clause."""
    lang = (ai_cfg.get("output_language") or "auto").lower()
    clause = _LANG_CLAUSE.get(lang, _LANG_CLAUSE["auto"])
    return SYSTEM_PROMPT_BASE + clause


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge ``override`` onto a copy of ``base`` (recursively for dicts)."""
    out = dict(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_ai_config() -> dict:
    """Return the ``ai`` config merged onto defaults (defaults fill gaps)."""
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            user_ai = cfg.get("ai", {})
            merged = _deep_merge(DEFAULT_AI_CONFIG, user_ai)
            # A user-provided recipe list fully replaces the defaults.
            if isinstance(user_ai.get("recipes"), list) and user_ai["recipes"]:
                merged["recipes"] = user_ai["recipes"]
            return merged
    except (OSError, ValueError) as e:
        print(f"[AI] Failed to load config, using defaults: {e}")
    return dict(DEFAULT_AI_CONFIG)


def save_ai_config(ai_cfg: dict) -> bool:
    """Persist the ``ai`` section back into config.json, preserving the rest."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg["ai"] = ai_cfg
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)
        return True
    except (OSError, ValueError) as e:
        print(f"[AI] Failed to save config: {e}")
        return False


def patch_ai_config(updates: dict) -> bool:
    """Merge ``updates`` into the ``ai`` section of config.json in place.

    Only the given keys are written (no default bloat), preserving the rest of
    the file. Used for quick toggles like the output-language selector.
    """
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        ai = cfg.get("ai")
        if not isinstance(ai, dict):
            ai = {}
        ai.update(updates)
        cfg["ai"] = ai
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)
        return True
    except (OSError, ValueError) as e:
        print(f"[AI] Failed to patch config: {e}")
        return False


def resolve_api_key(backend_cfg: dict) -> str:
    """Return the API key: explicit value first, else the named env var."""
    key = (backend_cfg.get("api_key") or "").strip()
    if key:
        return key
    env_name = backend_cfg.get("api_key_env") or ""
    return (os.environ.get(env_name, "") or "").strip()
