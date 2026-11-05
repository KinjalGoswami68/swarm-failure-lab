"""D3, D17, D22: the HTTP transport, against scripted responses (no network)."""
import httpx
import pytest

import agent
import run

OK = {"choices": [{"message": {"content": '{"version":1,"value":47}', "reasoning": "47."}}],
      "usage": {"prompt_tokens": 10, "completion_tokens": 3}}


@pytest.fixture
def scripted(monkeypatch):
    """Install a client whose responses come from a list; return the list of requests seen."""
    seen = []

    def install(*responses):
        queue = list(responses)

        def handler(request):
            seen.append(request)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(agent, "_client",
                            httpx.Client(base_url=agent.ENDPOINT, transport=httpx.MockTransport(handler)))
        monkeypatch.setattr(agent.time, "sleep", lambda s: None)
        return seen
    return install


def test_success_parses_content_reasoning_and_usage(scripted):
    seen = scripted(httpx.Response(200, json=OK))
    result = agent.call_model("m", 0.7, None, "prompt")
    assert (result["raw_text"], result["reasoning"], result["retries"]) == ('{"version":1,"value":47}', "47.", 0)
    assert (result["prompt_tokens"], result["completion_tokens"]) == (10, 3)
    assert seen[0].url.path.endswith("/chat/completions")
    assert b"reasoning_effort" not in seen[0].content           # not sent unless set (D11)


def test_reasoning_effort_is_sent_when_set(scripted):
    seen = scripted(httpx.Response(200, json=OK))
    agent.call_model("m", 0.7, "low", "prompt")
    assert b'"reasoning_effort":"low"' in seen[0].content.replace(b" ", b"")


def test_429_and_503_are_retried(scripted):
    scripted(httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(503), httpx.Response(200, json=OK))
    assert agent.call_model("m", 0.7, None, "prompt")["retries"] == 2


def test_connection_errors_are_retried_then_stop_the_run(scripted):
    scripted(*[httpx.ConnectError("down")] * (agent.MAX_RETRIES + 1))
    with pytest.raises(agent.RateLimitStop):
        agent.call_model("m", 0.7, None, "prompt")


def test_long_retry_after_stops_at_once(scripted):
    seen = scripted(httpx.Response(429, headers={"retry-after": "3600"}))
    with pytest.raises(agent.RateLimitStop):
        agent.call_model("m", 0.7, None, "prompt")
    assert len(seen) == 1                                       # D17: no sleeping on a daily cap


def test_client_errors_are_failed_trials_not_retried(scripted):
    seen = scripted(httpx.Response(400, json={"error": "bad request"}))
    with pytest.raises(agent.CallFailed):
        agent.call_model("m", 0.7, None, "prompt")
    assert len(seen) == 1


def test_non_default_endpoint_gets_its_own_results_folder(monkeypatch):
    monkeypatch.setattr(agent, "ENDPOINT", "http://localhost:11434/v1")
    assert run.model_dir("llama3/8b").name == "localhost_11434__llama3_8b"
    monkeypatch.setattr(agent, "ENDPOINT", agent.DEFAULT_ENDPOINT)
    assert run.model_dir("llama3/8b").name == "llama3_8b"
