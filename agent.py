"""One agent call: send a prompt to a Groq model, parse its decision artifact, cache the response."""
import hashlib
import json
import os
import re
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "cache"
MAX_RETRIES = 6
MAX_WAIT_S = 120  # a longer retry-after usually means a daily cap: stop instead of sleeping

# The artifact Acyclic's task asks for: {"version":1,"value":<integer>}.
# Whitespace between tokens is allowed; version must be 1 and value an integer.
EXACT = re.compile(r'\{\s*"version"\s*:\s*1\s*,\s*"value"\s*:\s*(-?\d+)\s*\}')
# Same JSON object with the keys swapped. Counted as valid, but not format_exact.
REORDERED = re.compile(r'\{\s*"value"\s*:\s*(-?\d+)\s*,\s*"version"\s*:\s*1\s*\}')

_client = None


def parse_artifact(text):
    """Return (value, format_exact).

    value is the artifact's integer, or None if there is no artifact or artifacts disagree.
    format_exact is True only if a value was found and every artifact used Acyclic's key order.
    """
    exact = EXACT.findall(text or "")
    reordered = REORDERED.findall(text or "")
    values = {int(v) for v in exact + reordered}
    if len(values) != 1:
        return None, False
    return values.pop(), not reordered


def parse_value(text):
    return parse_artifact(text)[0]


def cache_key(model, temperature, reasoning_effort, prompt, call_id):
    """call_id = [task, replicate, arm, hop]: identical prompts in different calls never share (D12)."""
    payload = json.dumps([model, temperature, reasoning_effort, prompt, call_id])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def client():
    global _client
    if _client is None:
        import truststore
        from dotenv import load_dotenv
        from groq import Groq
        # Verify TLS against the OS certificate store (needed behind HTTPS-scanning antivirus).
        # Certificate verification stays on.
        truststore.inject_into_ssl()
        load_dotenv()
        # max_retries=0: the SDK must not retry silently; every retry happens below and is counted.
        _client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)
    return _client


class CallFailed(Exception):
    """The model call failed in a way that counts as a failed trial (D3)."""
    def __init__(self, message, retries):
        super().__init__(message)
        self.retries = retries


class RateLimitStop(Exception):
    """Rate limit or connection errors outlasted the backoff. Not a trial outcome:
    the whole run must stop and be resumed later (D17). call_agent does not catch this."""


def call_model(model, temperature, reasoning_effort, prompt):
    """Fresh context every call: one user message, no system prompt, no history.

    reasoning_effort=None means the parameter is not sent (provider default).
    Retries only when no model response arrived: 429 rate limit, connection error, timeout, 5xx.
    """
    import groq

    extra = {} if reasoning_effort is None else {"reasoning_effort": reasoning_effort}
    for attempt in range(MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            resp = client().chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
                **extra,
            )
        # No model response arrived: 429, connection error/timeout, or 5xx server error (D3).
        except (groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError) as e:
            wait = 2 ** attempt
            if isinstance(e, groq.RateLimitError):
                wait = float(e.response.headers.get("retry-after", wait))
            if attempt == MAX_RETRIES or wait > MAX_WAIT_S:
                raise RateLimitStop(f"{type(e).__name__} after {attempt} retries "
                                    f"(next wait would be {wait:.0f}s): {e}")
            time.sleep(wait)
            continue
        except Exception as e:
            raise CallFailed(f"{type(e).__name__}: {e}", attempt)
        message = resp.choices[0].message
        return {
            "raw_text": message.content,
            "reasoning": message.reasoning,  # logged only; never passed to the next hop
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "retries": attempt,
        }


def call_agent(model, temperature, prompt, reasoning_effort=None, call_id=None, use_cache=True):
    """Return a result dict. Never raises: a failed call has value None and an error message.

    use_cache=False neither reads nor writes the cache (for fresh repeated probes).
    """
    key = cache_key(model, temperature, reasoning_effort, prompt, call_id)
    path = CACHE_DIR / f"{key}.json"

    if use_cache and path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        cached = True
    else:
        try:
            record = call_model(model, temperature, reasoning_effort, prompt)
        except CallFailed as e:
            return {"value": None, "format_exact": False, "raw_text": None, "reasoning": None,
                    "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": None,
                    "retries": e.retries, "reasoning_effort": reasoning_effort,
                    "cached": False, "error": str(e)}
        record.update(model=model, temperature=temperature,
                      reasoning_effort=reasoning_effort, prompt=prompt, call_id=call_id)
        if use_cache:
            CACHE_DIR.mkdir(exist_ok=True)
            path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        cached = False

    value, format_exact = parse_artifact(record["raw_text"])
    return {
        "value": value,
        "format_exact": format_exact,
        "raw_text": record["raw_text"],
        "reasoning": record["reasoning"],
        "prompt_tokens": record["prompt_tokens"],
        "completion_tokens": record["completion_tokens"],
        "latency_ms": record["latency_ms"],
        "retries": record["retries"],  # from the original call, also when served from cache
        "reasoning_effort": reasoning_effort,
        "cached": cached,
        "error": None,
    }
