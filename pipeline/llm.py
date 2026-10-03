"""One OpenRouter client for every LLM step (judge, stance, proposal coding, tie-breaks).

complete(prompt, schema, model) -> dict validated against `schema`.
- OpenAI-compatible client pointed at OpenRouter.
- JSON-schema structured output, provider.require_parameters, temperature 0, fixed seed.
- max_tokens always set; reasoning disabled.
- sqlite cache keyed by hash(model, system, prompt, schema, seed).
- Retries with backoff on 429/5xx; one schema-repair retry.
- Token / cost log appended to data/llm_costs.csv.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from . import config

_client: OpenAI | None = None
_lock = threading.Lock()


class LLMError(RuntimeError):
    pass


def client() -> OpenAI:
    global _client
    if _client is None:
        import os

        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise LLMError("OPENROUTER_API_KEY missing from .env")
        _client = OpenAI(base_url=config.OPENROUTER_BASE_URL, api_key=key, timeout=120)
    return _client


# --- cache -----------------------------------------------------------------
def _db() -> sqlite3.Connection:
    con = sqlite3.connect(config.LLM_CACHE, timeout=30, check_same_thread=False)
    con.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, model TEXT, response TEXT, ts REAL)")
    return con


def _key(model: str, system: str, prompt: str, schema: dict, seed: int) -> str:
    blob = json.dumps([model, system, prompt, schema, seed], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _cache_get(k: str):
    with _lock:
        con = _db()
        row = con.execute("SELECT response FROM cache WHERE k=?", (k,)).fetchone()
        con.close()
    return json.loads(row[0]) if row else None


def _cache_put(k: str, model: str, value: dict) -> None:
    with _lock:
        con = _db()
        con.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)", (k, model, json.dumps(value, ensure_ascii=False), time.time()))
        con.commit()
        con.close()


def _log_cost(model: str, usage: Any, tag: str) -> None:
    if usage is None:
        return
    u = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    new = not config.LLM_COST_LOG.exists()
    with _lock, open(config.LLM_COST_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "run_id", "tag", "model", "prompt_tokens", "completion_tokens", "cost"])
        w.writerow([time.time(), config.RUN_ID, tag, model, u.get("prompt_tokens"), u.get("completion_tokens"), u.get("cost")])


# --- validation ------------------------------------------------------------
def _validate(obj: Any, schema: dict) -> None:
    """Minimal JSON-schema check (object/required/enum/types), enough for our flat schemas."""
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if t in types and not isinstance(obj, types[t]):
        raise ValueError(f"expected {t}, got {type(obj).__name__}")
    if "enum" in schema and obj not in schema["enum"]:
        raise ValueError(f"{obj!r} not in {schema['enum']}")
    if t == "object":
        for r in schema.get("required", []):
            if r not in obj:
                raise ValueError(f"missing {r}")
        for k, sub in schema.get("properties", {}).items():
            if k in obj and obj[k] is not None:
                _validate(obj[k], sub)
    if t == "array" and "items" in schema:
        for x in obj:
            _validate(x, schema["items"])


def _retryable(e: BaseException) -> bool:
    if isinstance(e, (RateLimitError, APIConnectionError)):
        return True
    return isinstance(e, APIStatusError) and e.status_code >= 500


@retry(retry=retry_if_exception(_retryable), wait=wait_exponential(min=2, max=60), stop=stop_after_attempt(6), reraise=True)
def _call(model: str, messages: list, schema: dict, max_tokens: int, seed: int, stream: bool = False):
    # With require_parameters, every extra parameter must be supported or OpenRouter finds no route (404):
    # - seed is not sent (Anthropic endpoints don't support it; it stays in the cache key);
    # - reasoning is sent only to reasoning models (openai/gpt-4o rejects it).
    extra = {"provider": {"require_parameters": True}, "usage": {"include": True}}
    if not model.startswith("openai/gpt-4"):
        extra["reasoning"] = {"enabled": False}
    return client().chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=max_tokens,
        stream=stream,
        response_format={"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}},
        extra_body=extra,
    )


def _parse(text: str) -> Any:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    return json.loads(text)


def complete(
    prompt: str,
    schema: dict,
    model: str | None = None,
    system: str = "You are a careful analyst. Answer only with JSON that matches the schema.",
    max_tokens: int = config.JUDGE_MAX_TOKENS,
    seed: int = config.LLM_SEED,
    tag: str = "",
    use_cache: bool = True,
) -> dict:
    model = model or config.LLM_MODEL
    k = _key(model, system, prompt, schema, seed)
    if use_cache and (hit := _cache_get(k)) is not None:
        return hit
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    last_err: Exception | None = None
    for attempt in range(2):  # one repair retry on invalid JSON / schema mismatch
        resp = _call(model, messages, schema, max_tokens, seed)
        _log_cost(model, getattr(resp, "usage", None), tag)
        content = resp.choices[0].message.content if resp.choices else ""
        try:
            obj = _parse(content)
            _validate(obj, schema)
            if use_cache:
                _cache_put(k, model, obj)
            return obj
        except Exception as e:  # noqa: BLE001
            last_err = e
            messages = messages + [
                {"role": "assistant", "content": content or ""},
                {"role": "user", "content": f"That answer was invalid ({e}). Reply again with JSON matching the schema only."},
            ]
    raise LLMError(f"invalid answer from {model}: {last_err}")


def stream(prompt: str, schema: dict, model: str | None = None, system: str = "", max_tokens: int = config.JUDGE_MAX_TOKENS,
           tag: str = "stream"):
    """Yield the answer text as it arrives (for the live demo). Same routing parameters as complete()."""
    model = model or config.LLM_MODEL
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    for ev in _call(model, messages, schema, max_tokens, config.LLM_SEED, stream=True):
        if getattr(ev, "usage", None):
            _log_cost(model, ev.usage, tag)
        if ev.choices and ev.choices[0].delta and ev.choices[0].delta.content:
            yield ev.choices[0].delta.content


def batch(items: Iterable[Any], fn: Callable[[Any], dict], concurrency: int = config.LLM_CONCURRENCY) -> list:
    """Run fn over items concurrently. Failures come back as {'error': str} so one bad pair doesn't sink a batch."""

    def safe(x):
        try:
            return fn(x)
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        return list(ex.map(safe, items))


def credits() -> dict:
    """Remaining OpenRouter credit (GET /credits). Useful before a big batch."""
    import os
    import urllib.request

    req = urllib.request.Request(
        config.OPENROUTER_BASE_URL + "/credits", headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)["data"]
    return {"total_credits": d.get("total_credits"), "total_usage": d.get("total_usage"),
            "remaining": (d.get("total_credits") or 0) - (d.get("total_usage") or 0)}


if __name__ == "__main__":  # smoke test: python -m pipeline.llm
    print("credits:", credits())
    schema = {"type": "object", "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}},
              "required": ["answer"], "additionalProperties": False}
    print(complete("Is the EU AI Act a regulation? Answer yes or no.", schema, max_tokens=20, tag="smoke", use_cache=False))
