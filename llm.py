"""LLM call wrapper. Uses `claude -p` headless mode to call Claude via the
user's Claude Code subscription. Returns parsed JSON.

Why `claude -p` not Anthropic SDK: the user is on Pro subscription, not API
billing. `claude -p` consumes the subscription quota.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

MODEL_DEFAULT = "claude-sonnet-4-6"

# Match the largest balanced-looking JSON object: greedy from first { to last }
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)
_JSON_ARR_RE = re.compile(r"\[.*\]", re.S)

DEBUG_DIR = Path(__file__).resolve().parent / "logs"


class LLMError(Exception):
    pass


def _dump_debug(content: str, tag: str = "raw") -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    p = DEBUG_DIR / f"llm_debug_{tag}_{int(time.time())}.txt"
    p.write_text(content, encoding="utf-8")
    return p


def call_json(
    prompt: str,
    model: str = MODEL_DEFAULT,
    timeout: int = 120,
    max_retries: int = 1,
) -> tuple[dict | list, float]:
    """Run `claude -p` and parse the response as JSON. Returns (parsed, elapsed_s)."""
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        t0 = time.time()
        try:
            proc = subprocess.run(
                ["claude", "-p", "--model", model, "--output-format", "text"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode != 0:
                raise LLMError(f"claude -p rc={proc.returncode}: {proc.stderr.strip()[:300]}")
            out = proc.stdout.strip()
            try:
                parsed = _extract_json(out)
            except json.JSONDecodeError as e:
                p = _dump_debug(out, tag="parse_fail")
                raise LLMError(f"JSON parse failed: {e}; raw saved to {p}") from e
            return parsed, time.time() - t0
        except (subprocess.TimeoutExpired, LLMError) as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise LLMError(f"after {max_retries+1} attempts: {e!r}") from e
    raise LLMError(f"unreachable: {last_err!r}")


def _extract_json(text: str):
    """Try to parse as JSON; if there's prefix/suffix junk, extract the largest
    balanced JSON object/array."""
    text = text.strip()
    # strip code fences if any
    if text.startswith("```"):
        lines = text.splitlines()
        # drop lines that ARE code fences (start with ```)
        text = "\n".join(l for l in lines if not l.lstrip().startswith("```"))
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # find outermost {...} by indexing first '{' and last '}'
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last > first:
            candidate = text[first:last + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        # fallback: array
        first = text.find("[")
        last = text.rfind("]")
        if first != -1 and last > first:
            return json.loads(text[first:last + 1])
        raise


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else (
        '严格只输出 JSON，不要任何前后缀。模式：{"a": <int>, "b": "<str>"}。'
        '请返回 a=42, b="hi"。'
    )
    data, t = call_json(p)
    print(f"got in {t:.1f}s: {data!r}")
