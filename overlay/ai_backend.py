"""
JuhRadial MX - AI backend dispatch

Turns a recipe instruction + selected text into transformed text using one of
three interchangeable backends:

  - cli       : spawn an agent CLI (default Claude Code ``claude -p``), no API key.
  - anthropic : Anthropic Messages API.
  - openai    : OpenAI Chat Completions API.

All backends share the same ``generate()`` entry point and return plain text.
Errors are raised as ``AIBackendError`` with a human-readable message.

SPDX-License-Identifier: GPL-3.0
"""

import json
import shutil
import subprocess
import urllib.error
import urllib.request

from ai_config import effective_system_prompt, resolve_api_key


class AIBackendError(Exception):
    """Raised when an AI backend fails (missing key, network, CLI error)."""


def build_user_message(recipe_prompt: str, selected_text: str) -> str:
    """Combine a recipe instruction with the selected text."""
    text = selected_text or ""
    if "{text}" in recipe_prompt:
        return recipe_prompt.replace("{text}", text)
    return f"{recipe_prompt}\n\n---\n{text}".rstrip()


def generate(recipe_prompt: str, selected_text: str, ai_cfg: dict) -> str:
    """Dispatch to the configured backend and return transformed text."""
    backend = (ai_cfg.get("backend") or "cli").lower()
    system = effective_system_prompt(ai_cfg)
    if backend == "cli":
        return _generate_cli(recipe_prompt, selected_text, ai_cfg.get("cli", {}), system)
    if backend == "anthropic":
        return _generate_anthropic(recipe_prompt, selected_text, ai_cfg.get("anthropic", {}), system)
    if backend == "openai":
        return _generate_openai(recipe_prompt, selected_text, ai_cfg.get("openai", {}), system)
    raise AIBackendError(f"Unknown AI backend: {backend!r}")


# ---------------------------------------------------------------------------
# CLI backend (default - reuses an installed agent's login, no API key)
# ---------------------------------------------------------------------------


def _generate_cli(recipe_prompt: str, selected_text: str, cli_cfg: dict, system: str) -> str:
    template = cli_cfg.get("command") or ["claude", "-p", "{prompt}"]
    model = cli_cfg.get("model", "sonnet")
    timeout_s = int(cli_cfg.get("timeout_s", 60))

    # The instruction goes in {prompt}; the selected text is piped via stdin so
    # it never needs shell-escaping. Fold the system guardrail into the prompt.
    instruction = f"{system}\n\nTask: {recipe_prompt}\n\nThe text to transform is provided on stdin."

    argv = []
    for part in template:
        argv.append(part.replace("{prompt}", instruction).replace("{model}", model))

    exe = argv[0] if argv else ""
    if not shutil.which(exe):
        raise AIBackendError(
            f"CLI '{exe}' not found in PATH. Install it (e.g. Claude Code) or "
            f"switch the AI backend to an API in settings."
        )

    try:
        proc = subprocess.run(
            argv,
            input=selected_text or "",
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise AIBackendError(f"CLI '{exe}' timed out after {timeout_s}s.")
    except OSError as e:
        raise AIBackendError(f"Failed to run CLI '{exe}': {e}")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise AIBackendError(f"CLI '{exe}' exited {proc.returncode}: {detail[:300]}")

    out = (proc.stdout or "").strip()
    if not out:
        raise AIBackendError(f"CLI '{exe}' returned no output.")
    return out


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _http_post_json(url: str, headers: dict, payload: dict, timeout_s: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        raise AIBackendError(f"HTTP {e.code} from {url}: {body[:300]}")
    except urllib.error.URLError as e:
        raise AIBackendError(f"Network error contacting {url}: {e.reason}")
    except (ValueError, TimeoutError) as e:
        raise AIBackendError(f"Bad response from {url}: {e}")


# ---------------------------------------------------------------------------
# Anthropic Messages API
# ---------------------------------------------------------------------------


def _generate_anthropic(recipe_prompt: str, selected_text: str, cfg: dict, system: str) -> str:
    key = resolve_api_key(cfg)
    if not key:
        raise AIBackendError(
            "No Anthropic API key set. Add one in AI settings or export "
            f"{cfg.get('api_key_env', 'ANTHROPIC_API_KEY')}."
        )
    base = (cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
    payload = {
        "model": cfg.get("model", "claude-sonnet-4-6"),
        "max_tokens": int(cfg.get("max_tokens", 1024)),
        "system": system,
        "messages": [
            {"role": "user", "content": build_user_message(recipe_prompt, selected_text)}
        ],
    }
    headers = {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    resp = _http_post_json(f"{base}/v1/messages", headers, payload, int(cfg.get("timeout_s", 60)))
    try:
        parts = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
        text = "".join(parts).strip()
    except (AttributeError, TypeError):
        text = ""
    if not text:
        raise AIBackendError("Anthropic API returned no text.")
    return text


# ---------------------------------------------------------------------------
# OpenAI Chat Completions API
# ---------------------------------------------------------------------------


def _generate_openai(recipe_prompt: str, selected_text: str, cfg: dict, system: str) -> str:
    key = resolve_api_key(cfg)
    if not key:
        raise AIBackendError(
            "No OpenAI API key set. Add one in AI settings or export "
            f"{cfg.get('api_key_env', 'OPENAI_API_KEY')}."
        )
    base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": cfg.get("model", "gpt-4o"),
        "max_tokens": int(cfg.get("max_tokens", 1024)),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_message(recipe_prompt, selected_text)},
        ],
    }
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {key}",
    }
    resp = _http_post_json(f"{base}/chat/completions", headers, payload, int(cfg.get("timeout_s", 60)))
    try:
        text = resp["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        text = ""
    if not text:
        raise AIBackendError("OpenAI API returned no text.")
    return text
