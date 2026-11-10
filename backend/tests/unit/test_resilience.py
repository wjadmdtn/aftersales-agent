"""重试与退避。"""

import pytest

from app.agent.llm.base import LLMError
from app.agent.resilience import RetryPolicy, RetryStats, with_retry


def test_backoff_schedule():
    p = RetryPolicy(base_delay_s=0.2, multiplier=3.0)
    assert [p.delay_for(i) for i in range(3)] == pytest.approx([0.2, 0.6, 1.8])


async def test_succeeds_first_try():
    calls = []

    async def fn():
        calls.append(1)
        return "ok"

    assert await with_retry(fn, policy=RetryPolicy(attempts=3, base_delay_s=0)) == "ok"
    assert len(calls) == 1


async def test_retries_then_succeeds():
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise LLMError("boom")
        return "ok"

    assert await with_retry(fn, policy=RetryPolicy(attempts=3, base_delay_s=0)) == "ok"
    assert len(calls) == 3


async def test_exhausts_then_raises():
    async def fn():
        raise LLMError("always fails")

    with pytest.raises(LLMError):
        await with_retry(fn, policy=RetryPolicy(attempts=3, base_delay_s=0))


def test_stats_failure_rate():
    s = RetryStats()
    s.total_calls = 10
    s.failures = 3
    assert s.failure_rate == 0.3


def test_stats_retry_rate():
    s = RetryStats()
    s.total_calls = 10
    s.retried_successes = 2
    assert s.retry_rate == 0.2


def test_stats_empty_is_zero():
    s = RetryStats()
    assert s.failure_rate == 0.0
    assert s.retry_rate == 0.0
