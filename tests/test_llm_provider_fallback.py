"""`_call_llm`'s own logic -- the 305 lines every other test mocks past.

Audit bar 3, 2026-08-12, agents subsystem. Replacing the whole body with
`return {"status": "error", "error": "gutted"}` left all 150 agent tests green.
The reason is visible in the numbers: 23 test files reference `_call_llm` and
65 of those references are `patch(... _call_llm ...)`. It is the most-mocked
function in the repository and, until this file, the least tested. Universal
mocking reads as heavy coverage and is its exact opposite -- every one of those
tests asserts what happens AFTER this function returns.

What lives in here and had no detection:

  * provider fallback on retryable failures, and the deliberate REFUSAL to
    fall back on the others
  * `tool_choice`, decided per-attempt -- the recorded root cause of all 76
    deterministic calculators being unforceable on Kimi
  * temperature, decided per-attempt -- Moonshot K2 rejects any value but 1
  * recovery of Llama-native tool markup out of a Groq HTTP 400
  * the soft daily cost cap

Mocked at `httpx.AsyncClient.post`, which is the real boundary: everything
above it is this function's own work.

ENV ISOLATION IS LOAD-BEARING. `_llm_config` reads LLM_PROVIDER, KIMI_API_KEY,
GROQ_API_KEY and the per-provider model overrides live on every call, with no
caching, and `_llm_fallback_config` temporarily MUTATES os.environ. A developer
machine with a real KIMI_API_KEY exported, or a CI leg that pins LLM_PROVIDER,
would silently take a different branch than the test intends -- and a fallback
test whose second attempt never existed passes for the wrong reason. So the
fixture clears every var first, and `test_the_attempt_list_is_what_these_tests
_assume` exists to catch a leak that collapses two attempts into one.
"""
from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest

from app.agents.runtime import GROQ_API_URL, KIMI_API_URL, Agent

# Every env var that can change which branch runs. Cleared before each test.
_LLM_ENV = (
    "LLM_PROVIDER", "LLM_FALLBACK_PROVIDER",
    "KIMI_API_KEY", "KIMI_MODEL", "KIMI_FALLBACK_MODEL",
    "GROQ_API_KEY", "GROQ_MODEL",
    "OLLAMA_API_KEY", "OLLAMA_URL", "OLLAMA_MODEL",
    "USAGE_DAILY_CAP_USD", "FORCE_CALC_ON_DIMENSIONS",
)

OLLAMA_OAI_URL = "http://test-ollama:11434/v1/chat/completions"


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    """No inherited env may reach `_llm_config`."""
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)


class _Resp:
    """The parts of an httpx.Response `_call_llm` touches."""

    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else _ok_body()
        self.text = text if text is not None else json.dumps(self._payload)
        # llm_client wraps a >=400 response in HTTPStatusError(request=r.request,
        # response=r). Without this attribute that construction raises
        # AttributeError, which its generic except then misreads as a network
        # error and RETRIES -- inverting what the 401 test asserts.
        self.request = None

    def json(self):
        return self._payload


def _ok_body(content="an answer", model="some-model", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"model": model, "choices": [{"message": message}],
            "usage": {"total_tokens": 10}}


def _tool_use_failed_body(failed_generation):
    return {"error": {"code": "tool_use_failed",
                      "failed_generation": failed_generation}}


class _Http:
    """Records every outbound call and replays a scripted response per call.

    A response may be an exception instance, which is raised -- that is how the
    timeout and transport-error branches are reached.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def post(self, url, json=None, headers=None, **kw):
        # DEEP COPY, and not incidentally. `_call_llm` builds ONE payload dict
        # and rebinds `model`, `temperature` and `tool_choice` on it before
        # each attempt, so storing the reference makes every recorded call show
        # the LAST attempt's values. That is not a cosmetic problem: the
        # per-attempt tests below exist precisely to catch a fallback that
        # carries the primary's pinned temperature across, and a by-reference
        # recorder reports [0.3, 0.3] for both the correct and the broken
        # implementation.
        self.calls.append({"url": url, "payload": deepcopy(json), "headers": headers})
        nxt = self.script.pop(0) if self.script else _Resp()
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    # -- readability helpers used by the assertions ------------------------
    @property
    def urls(self):
        return [c["url"] for c in self.calls]

    @property
    def models(self):
        return [c["payload"].get("model") for c in self.calls]

    @property
    def tool_choices(self):
        return [c["payload"].get("tool_choice") for c in self.calls]

    @property
    def temperatures(self):
        return [c["payload"].get("temperature") for c in self.calls]


@pytest.fixture
def http(monkeypatch):
    def _install(*script):
        fake = _Http(script)
        monkeypatch.setattr(httpx.AsyncClient, "post", fake.post, raising=True)
        return fake

    return _install


def _agent(name="project-assistant", model="kimi-k2.6", temperature=0.3,
           blocks=("construction",)):
    return Agent(
        name=name,
        description="test agent",
        system_prompt="you are a test",
        allowed_blocks=list(blocks),
        model=model,
        temperature=temperature,
    )


def _kimi_primary(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "kimi-test-key")


def _groq_fallback(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")


USER = [{"role": "user", "content": "What is the notice period?"}]
DELIVERABLE = [{"role": "user", "content": "Generate a construction schedule with 200 activities"}]


# ── the guard ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_attempt_list_is_what_these_tests_assume(monkeypatch, http):
    """Without this, a leaked env that leaves no fallback configured makes
    every "did not fall back" test below pass for the wrong reason -- there was
    never a second attempt to take.

    Asserts the positive shape: two attempts, at two DIFFERENT providers'
    URLs, in the documented order.
    """
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(_Resp(429, text="rate limited"), _Resp(200))

    await _agent()._call_llm(list(USER), "kimi-test-key")

    assert fake.urls == [KIMI_API_URL, GROQ_API_URL], (
        f"the ladder is not kimi -> groq: {fake.urls}"
    )


@pytest.mark.asyncio
async def test_with_no_fallback_configured_there_is_exactly_one_attempt(monkeypatch, http):
    """The other half of the guard. If this ever shows 2, an env var leaked in
    and the no-fallback tests are meaningless."""
    _kimi_primary(monkeypatch)
    fake = http(_Resp(500, text="server error"))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert len(fake.calls) == 1, f"an unconfigured fallback was attempted: {fake.urls}"
    assert result["status"] == "error"


# ── fallback: the retryable / non-retryable fence ────────────────────────

@pytest.mark.parametrize("status", [408, 413, 429, 500, 502, 503])
@pytest.mark.asyncio
async def test_a_retryable_status_falls_back_to_the_next_provider(monkeypatch, http, status):
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(_Resp(status, text="upstream said no"), _Resp(200, _ok_body("recovered")))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert len(fake.calls) == 2, f"HTTP {status} did not trigger the fallback"
    assert result["status"] == "success", result
    assert result["choice"]["message"]["content"] == "recovered"


@pytest.mark.parametrize("status", [400, 401, 403, 404])
@pytest.mark.asyncio
async def test_a_non_retryable_status_does_not_fall_back(monkeypatch, http, status):
    """The half of the fence that is easy to lose.

    A different provider cannot fix a bad key or a malformed request, so
    retrying burns the fallback's quota and doubles the user's wait for the
    same error. Both directions are asserted because a one-sided check leaves
    the other open -- which is the failure this whole audit started from.
    """
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(_Resp(status, text="bad request"), _Resp(200, _ok_body("should never run")))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert len(fake.calls) == 1, (
        f"HTTP {status} is not retryable but a second provider was called anyway: "
        f"{fake.urls}"
    )
    assert result["status"] == "error", result
    assert str(status) in result["error"], result
    assert "kimi" in result["error"], "the failing provider is not named in the error"


@pytest.mark.asyncio
async def test_a_timeout_falls_back(monkeypatch, http):
    """The Kimi-primary failure mode with a name: K2 is a slow reasoning model
    and a large grounded turn times out. The documented reason a fallback
    exists at all."""
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(httpx.TimeoutException("read timeout"), _Resp(200, _ok_body("recovered")))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert len(fake.calls) == 2, "a timeout did not fall back"
    assert result["status"] == "success", result


@pytest.mark.asyncio
async def test_a_transport_error_falls_back(monkeypatch, http):
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(httpx.ConnectError("dns failure"), _Resp(200, _ok_body("recovered")))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert len(fake.calls) == 2, "a connect error did not fall back"
    assert result["status"] == "success", result


@pytest.mark.asyncio
async def test_when_every_provider_fails_the_last_error_is_returned(monkeypatch, http):
    """Not a generic "LLM call failed" -- the operator needs the provider and
    status that actually ended the turn."""
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(_Resp(429, text="kimi rate limited"), _Resp(503, text="groq unavailable"))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert len(fake.calls) == 2
    assert result["status"] == "error"
    assert "groq" in result["error"] and "503" in result["error"], (
        f"the surviving error is not the last provider's: {result['error']}"
    )


@pytest.mark.asyncio
async def test_the_same_provider_model_fallback_is_preferred_over_a_cross_provider_one(
        monkeypatch, http):
    """`KIMI_FALLBACK_MODEL` is documented as the RECOMMENDED fallback: same
    key, same endpoint, a fast big-context model, and no fixed-temperature
    constraint. It is checked BEFORE the cross-provider ladder, so configuring
    both must not send the turn to Groq."""
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    monkeypatch.setenv("KIMI_FALLBACK_MODEL", "moonshot-v1-128k")
    fake = http(_Resp(429, text="rate limited"), _Resp(200, _ok_body("recovered")))

    await _agent()._call_llm(list(USER), "kimi-test-key")

    assert fake.urls == [KIMI_API_URL, KIMI_API_URL], (
        f"the model fallback was skipped in favour of a cross-provider hop: {fake.urls}"
    )
    assert fake.models == ["kimi-k2.6", "moonshot-v1-128k"], fake.models


# ── temperature, decided per attempt ─────────────────────────────────────

@pytest.mark.asyncio
async def test_each_attempt_gets_the_temperature_its_own_provider_accepts(monkeypatch, http):
    """Moonshot K2 400s on any temperature but 1; Groq wants the agent's own.

    Asserting only the first payload would miss the regression that matters --
    a fallback that carries the primary's pinned 1 across is a 400 on the
    provider the turn just degraded to, i.e. the fallback fails BECAUSE it
    fell back.
    """
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(_Resp(429, text="rate limited"), _Resp(200))

    await _agent(temperature=0.3)._call_llm(list(USER), "kimi-test-key")

    assert fake.temperatures == [1.0, 0.3], (
        f"temperature is not being decided per attempt: {fake.temperatures}"
    )


# ── tool_choice, decided per attempt ─────────────────────────────────────

@pytest.mark.asyncio
async def test_kimi_is_never_sent_a_forced_tool_choice(monkeypatch, http):
    """Forcing a specific tool 400s on K2 ("tool_choice 'specified' is
    incompatible with thinking enabled"), and forcing on Groq makes
    Llama-4-Scout emit the tool as prose. Both must stay on "auto" even when
    the turn names a deliverable.

    This is also the recorded root cause of every deterministic calculator
    being unforceable in production, so it is pinned deliberately rather than
    left as an implementation detail: the constraint is the provider's, and a
    future change must be a change to THIS assertion.
    """
    _kimi_primary(monkeypatch)
    fake = http(_Resp(200))

    await _agent()._call_llm(list(DELIVERABLE), "kimi-test-key")

    assert fake.tool_choices == ["auto"], (
        f"a forced tool_choice was sent to kimi: {fake.tool_choices}"
    )


@pytest.mark.asyncio
async def test_a_provider_that_honours_forcing_gets_the_tool_by_name(monkeypatch, http):
    """Discriminates "correctly returns auto" from "never computed a forced
    tool at all".

    The test above passes either way, because groq/kimi return "auto" before
    `forced_tool` is ever consulted. Only a provider past that early return
    shows whether the intent was resolved. Without this, gutting
    `_forced_specific_tool` would go undetected here.

    The forced tool must be one this agent actually ADVERTISES --
    `_forced_specific_tool` only returns names present in the turn's tool set.
    A first draft used "primavera" and got `"required"` back, because the
    agent's tools are remember_fact / generate_wbs / commissioning_checklist /
    construction_calc and primavera_parser was never among them. "earned value"
    maps to construction_calc, which is.
    """
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", OLLAMA_OAI_URL)
    fake = http(_Resp(200))

    messages = [{"role": "user", "content": "Give me the earned value position"}]
    await _agent()._call_llm(messages, "")

    assert fake.tool_choices == [
        {"type": "function", "function": {"name": "construction_calc"}}
    ], (
        "the named-tool force was not applied on a provider that honours it -- "
        f"got {fake.tool_choices}"
    )


@pytest.mark.asyncio
async def test_a_deliverable_with_no_specific_tool_is_merely_required(monkeypatch, http):
    """`required` alone let the model satisfy it with the WRONG tool, which is
    why named forcing exists. It remains correct when no single tool owns the
    intent."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", OLLAMA_OAI_URL)
    fake = http(_Resp(200))

    messages = [{"role": "user", "content": "Produce a cost estimate for the works"}]
    await _agent()._call_llm(messages, "")

    assert fake.tool_choices == ["required"], fake.tool_choices


@pytest.mark.asyncio
async def test_an_ordinary_question_is_not_forced_to_call_a_tool(monkeypatch, http):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", OLLAMA_OAI_URL)
    fake = http(_Resp(200))

    await _agent()._call_llm(list(USER), "")

    assert fake.tool_choices == ["auto"], fake.tool_choices


@pytest.mark.asyncio
async def test_only_the_gated_agents_force_a_specific_tool(monkeypatch, http):
    """Gated to project-assistant / heavy-reasoning because other agents may
    legitimately answer the same keywords in prose."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", OLLAMA_OAI_URL)
    fake = http(_Resp(200))

    messages = [{"role": "user", "content": "Parse the primavera xer baseline programme"}]
    await _agent(name="document-analyst")._call_llm(messages, "")

    assert fake.tool_choices == ["auto"], (
        f"a non-gated agent had a tool forced on it: {fake.tool_choices}"
    )


@pytest.mark.asyncio
async def test_with_tools_disabled_no_tools_and_no_tool_choice_are_sent(monkeypatch, http):
    """The synthesis call. A tool_choice on a tool-free payload is a 400."""
    _kimi_primary(monkeypatch)
    fake = http(_Resp(200))

    await _agent()._call_llm(list(DELIVERABLE), "kimi-test-key", with_tools=False)

    payload = fake.calls[0]["payload"]
    assert "tools" not in payload, "tools were sent with with_tools=False"
    assert "tool_choice" not in payload, payload.get("tool_choice")


@pytest.mark.asyncio
async def test_excluded_tools_are_withheld(monkeypatch, http):
    """Used to stop the loop re-offering a tool that already ran and failed."""
    _kimi_primary(monkeypatch)
    fake = http(_Resp(200))

    await _agent()._call_llm(list(USER), "kimi-test-key",
                             exclude_tools={"construction_calc"})

    names = {t["function"]["name"] for t in fake.calls[0]["payload"]["tools"]}
    assert "construction_calc" not in names, "an excluded tool was offered anyway"
    assert names, "every tool was dropped, not just the excluded one"


# ── Groq's tool_use_failed, both outcomes ────────────────────────────────

@pytest.mark.asyncio
async def test_llama_native_tool_markup_is_recovered_from_a_400(monkeypatch, http):
    """Groq's validator rejects Llama's own function markup with HTTP 400 and
    buries the markup in `error.failed_generation`. Recovering it turns a dead
    turn into a working tool call, so a regression here reads to the user as
    "the assistant randomly fails on some questions"."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    markup = '<function=construction_calc{"formula": "concrete_volume"}></function>'
    fake = http(_Resp(400, _tool_use_failed_body(markup), text=json.dumps(
        _tool_use_failed_body(markup))))

    result = await _agent()._call_llm(list(USER), "groq-test-key")

    assert result["status"] == "success", (
        f"recoverable tool markup was returned as an error instead: {result}"
    )
    calls = result["choice"]["message"]["tool_calls"]
    assert calls and calls[0]["function"]["name"] == "construction_calc", calls
    assert len(fake.calls) == 1, "a recovered call should not also fall back"


@pytest.mark.asyncio
async def test_a_tool_call_emitted_as_prose_falls_back_instead_of_erroring(monkeypatch, http):
    """The subtle half. Nothing can be recovered from prose, but it is a
    MODEL-side failure another provider can handle -- so it is retryable even
    though HTTP 400 normally is not. Without this the turn dies on an error the
    fallback would have answered."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", OLLAMA_OAI_URL)
    prose = "I will now calculate the concrete volume for you."
    body = _tool_use_failed_body(prose)
    fake = http(_Resp(400, body, text=json.dumps(body)),
                _Resp(200, _ok_body("recovered")))

    result = await _agent()._call_llm(list(USER), "groq-test-key")

    assert len(fake.calls) == 2, (
        "unrecoverable tool_use_failed did not fall back -- the turn dies on a "
        "400 the next provider could have answered"
    )
    assert result["status"] == "success", result


# ── model pinning ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_legacy_placeholder_model_is_replaced_by_the_provider_default(
        monkeypatch, http):
    """Agents still carry deepseek-/gpt-4 names from before those providers
    were removed. Sending one to Moonshot is a 404 on every turn that agent
    handles."""
    _kimi_primary(monkeypatch)
    monkeypatch.setenv("KIMI_MODEL", "kimi-k2.6")
    fake = http(_Resp(200))

    await _agent(model="deepseek-chat")._call_llm(list(USER), "kimi-test-key")

    assert fake.models == ["kimi-k2.6"], (
        f"a dead provider's model name was sent upstream: {fake.models}"
    )


@pytest.mark.asyncio
async def test_an_agent_that_pinned_a_live_model_keeps_it(monkeypatch, http):
    """The other direction: heavy-reasoning pins a model deliberately, and
    overriding it would silently downgrade that agent."""
    _kimi_primary(monkeypatch)
    monkeypatch.setenv("KIMI_MODEL", "kimi-k2.6")
    fake = http(_Resp(200))

    await _agent(model="moonshot-v1-128k")._call_llm(list(USER), "kimi-test-key")

    assert fake.models == ["moonshot-v1-128k"], fake.models


# ── the outbound sanitisation chokepoint ─────────────────────────────────

@pytest.mark.asyncio
async def test_non_standard_message_fields_never_reach_the_provider(monkeypatch, http):
    """A `reasoning` field on a tool-call message caused a Groq HTTP 400 that
    hung deliverable generation. `_call_llm` is the single chokepoint that
    strips it, so it is asserted at the wire rather than on the helper."""
    _kimi_primary(monkeypatch)
    fake = http(_Resp(200))

    messages = [{"role": "assistant", "content": "thinking",
                 "reasoning": "internal chain of thought"},
                {"role": "user", "content": "and now?"}]
    await _agent()._call_llm(messages, "kimi-test-key")

    sent = fake.calls[0]["payload"]["messages"]
    assert not any("reasoning" in m for m in sent), (
        f"a non-standard field reached the provider: {sent}"
    )
    assert len(sent) == 2, "sanitisation dropped whole messages"


# ── the soft daily cap ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_user_over_the_daily_cap_is_refused_before_any_spend(monkeypatch, http):
    """The point of a cost cap is that the request is never made. Returning the
    error AFTER calling the provider would cap nothing."""
    _kimi_primary(monkeypatch)
    monkeypatch.setenv("USAGE_DAILY_CAP_USD", "5.00")
    from app.core import usage_tracker
    monkeypatch.setattr(usage_tracker, "is_over_cap", lambda user_id, cap: True)
    monkeypatch.setattr(usage_tracker, "daily_total", lambda user_id: {"cost_usd": 7.5})
    fake = http(_Resp(200))

    result = await _agent()._call_llm(list(USER), "kimi-test-key", user_id="u1")

    assert fake.calls == [], "the provider was called despite the cap being hit"
    assert result["status"] == "error"
    assert "cap" in result["error"].lower(), result["error"]


@pytest.mark.asyncio
async def test_an_internal_call_with_no_user_is_not_capped(monkeypatch, http):
    """Documented: calls without a user_id aren't billable, so capping them
    would break internal work for a spend it never caused."""
    _kimi_primary(monkeypatch)
    monkeypatch.setenv("USAGE_DAILY_CAP_USD", "5.00")
    from app.core import usage_tracker
    monkeypatch.setattr(usage_tracker, "is_over_cap",
                        lambda user_id, cap: pytest.fail("cap checked with no user_id"))
    fake = http(_Resp(200))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert len(fake.calls) == 1 and result["status"] == "success", result


@pytest.mark.asyncio
async def test_a_broken_usage_tracker_does_not_block_the_call(monkeypatch, http):
    """Explicit in the code: a broken tracker must never block a real call.
    The failure mode it prevents is total -- every turn refused because a
    bookkeeping table is unreachable."""
    _kimi_primary(monkeypatch)
    monkeypatch.setenv("USAGE_DAILY_CAP_USD", "5.00")
    from app.core import usage_tracker

    def _boom(*a, **kw):
        raise RuntimeError("usage table unreachable")

    monkeypatch.setattr(usage_tracker, "is_over_cap", _boom)
    fake = http(_Resp(200))

    result = await _agent()._call_llm(list(USER), "kimi-test-key", user_id="u1")

    assert result["status"] == "success", result
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_an_unparseable_cap_disables_the_check(monkeypatch, http):
    _kimi_primary(monkeypatch)
    monkeypatch.setenv("USAGE_DAILY_CAP_USD", "not-a-number")
    from app.core import usage_tracker
    monkeypatch.setattr(usage_tracker, "is_over_cap",
                        lambda user_id, cap: pytest.fail("checked an unparseable cap"))
    fake = http(_Resp(200))

    result = await _agent()._call_llm(list(USER), "kimi-test-key", user_id="u1")
    assert result["status"] == "success" and len(fake.calls) == 1


# ── the success path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_successful_call_returns_the_providers_choice_and_raw_body(
        monkeypatch, http):
    _kimi_primary(monkeypatch)
    body = _ok_body("28 days under clause 20.1", model="kimi-k2.6")
    http(_Resp(200, body))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert result["status"] == "success"
    assert result["choice"]["message"]["content"] == "28 days under clause 20.1"
    assert result["raw"] == body, "the raw body is not forwarded"


@pytest.mark.asyncio
async def test_a_malformed_success_body_falls_back(monkeypatch, http):
    """A 200 whose body cannot be parsed is still a failed turn, and another
    provider can serve it."""
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)

    class _Garbage(_Resp):
        def json(self):
            raise ValueError("not json")

    fake = http(_Garbage(200), _Resp(200, _ok_body("recovered")))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert len(fake.calls) == 2, "an unparseable 200 did not fall back"
    assert result["status"] == "success", result


@pytest.mark.asyncio
async def test_the_api_key_is_sent_as_a_bearer_header(monkeypatch, http):
    _kimi_primary(monkeypatch)
    fake = http(_Resp(200))

    await _agent()._call_llm(list(USER), "kimi-test-key")

    assert fake.calls[0]["headers"]["Authorization"] == "Bearer kimi-test-key"


@pytest.mark.asyncio
async def test_a_keyless_provider_gets_no_authorization_header(monkeypatch, http):
    """Self-hosted Ollama has no auth. Sending `Bearer ` with an empty key is
    rejected by some gateways."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", OLLAMA_OAI_URL)
    fake = http(_Resp(200))

    await _agent()._call_llm(list(USER), "")

    assert "Authorization" not in fake.calls[0]["headers"], fake.calls[0]["headers"]


@pytest.mark.asyncio
async def test_the_fallback_attempt_uses_the_fallback_providers_own_key(
        monkeypatch, http):
    """The primary's key on the fallback's endpoint is a 401 -- a fallback that
    always fails, which looks like "the fallback provider is down"."""
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(_Resp(429, text="rate limited"), _Resp(200))

    await _agent()._call_llm(list(USER), "kimi-test-key")

    assert fake.calls[0]["headers"]["Authorization"] == "Bearer kimi-test-key"
    assert fake.calls[1]["headers"]["Authorization"] == "Bearer groq-test-key", (
        "the fallback was called with the primary's key"
    )


@pytest.mark.asyncio
async def test_a_successful_primary_never_touches_the_fallback(monkeypatch, http):
    """Success must not leak a second request -- a fallback that fires on
    success doubles cost and latency invisibly."""
    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(_Resp(200, _ok_body("primary ok")),
                _Resp(200, _ok_body("SHOULD NOT REACH")))

    result = await _agent()._call_llm(list(USER), "kimi-test-key")

    assert result["choice"]["message"]["content"] == "primary ok"
    assert len(fake.calls) == 1, f"the fallback was called on success: {fake.urls}"


@pytest.mark.asyncio
async def test_prose_tool_use_failed_with_no_fallback_is_an_error(monkeypatch, http):
    """The pair of the prose-falls-back test: with no fallback configured the
    same 400 must surface as an error, not be swallowed."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    body = _tool_use_failed_body("## prose checklist, not a tool call")
    fake = http(_Resp(400, body, text=json.dumps(body)))

    result = await _agent()._call_llm(list(USER), "groq-test-key")

    assert result["status"] == "error", result
    assert len(fake.calls) == 1


# ── _llm_fallback_config branch coverage ─────────────────────────────────
# Restored from the pre-2026-08-12 version of this file, which this suite
# replaced. Everything else it covered is covered more strictly above, but
# these unit branches and the llm_client tests below had no replacement.

def test_fallback_config_is_none_when_unset(monkeypatch):
    from app.agents.runtime import _llm_fallback_config

    assert _llm_fallback_config({"provider": "groq"}) is None


def test_fallback_config_is_none_when_it_names_the_primary(monkeypatch):
    """Falling back to the provider that just failed is a retry loop wearing a
    fallback's name."""
    from app.agents.runtime import _llm_fallback_config

    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    assert _llm_fallback_config({"provider": "groq"}) is None


def test_fallback_config_is_none_when_its_key_is_missing(monkeypatch):
    """A fallback with no key is an attempt that can only 401 -- worse than
    reporting the primary's real error."""
    from app.agents.runtime import _llm_fallback_config

    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert _llm_fallback_config({"provider": "kimi"}) is None


def test_fallback_config_resolves_ollama_with_url_normalisation(monkeypatch):
    from app.agents.runtime import _llm_fallback_config

    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://fallback.tunnel.cf")
    cfg = _llm_fallback_config({"provider": "kimi"})
    assert cfg is not None and cfg["provider"] == "ollama"
    assert cfg["url"].endswith("/v1/chat/completions"), cfg["url"]


# ── llm_client.complete(): the orchestrator intent path ─────────────────
# A different module with its own fallback ladder. If it stopped degrading,
# smart routing would silently vanish for the turn on every provider blip.

@pytest.mark.asyncio
async def test_orchestrator_complete_falls_back_on_a_rate_limit(monkeypatch, http):
    from app.core import llm_client

    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(_Resp(429, text="rate limited"), _Resp(200, _ok_body("intent json")))

    out = await llm_client.complete([{"role": "user", "content": "hi"}])

    assert out == "intent json"
    assert len(fake.calls) == 2, f"the intent path did not degrade: {fake.urls}"


@pytest.mark.asyncio
async def test_orchestrator_complete_does_not_fall_back_on_a_bad_key(monkeypatch, http):
    from app.core import llm_client

    _kimi_primary(monkeypatch)
    _groq_fallback(monkeypatch)
    fake = http(_Resp(401, text="invalid api key"),
                _Resp(200, _ok_body("SHOULD NOT REACH")))

    with pytest.raises(httpx.HTTPStatusError):
        await llm_client.complete([{"role": "user", "content": "hi"}])
    assert len(fake.calls) == 1, f"a 401 was retried on the intent path: {fake.urls}"
