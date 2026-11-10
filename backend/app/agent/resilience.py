"""LLM 调用的重试与退避。

只对**幂等且无副作用**的调用重试。意图判断不产生副作用，可以安全重试。

抖动大多是瞬时网络问题，重试一次就好；撑不过三轮才降级。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.agent.llm.base import LLMError

logger = logging.getLogger(__name__)


@dataclass
class RetryPolicy:
    attempts: int = 3
    base_delay_s: float = 0.2
    multiplier: float = 3.0

    def delay_for(self, attempt: int) -> float:
        """attempt 从 0 开始。0.2s → 0.6s → 1.8s"""
        return self.base_delay_s * (self.multiplier**attempt)


@dataclass
class RetryStats:
    """进程内累计，供告警与监控读取。"""

    total_calls: int = 0
    total_attempts: int = 0
    successes: int = 0
    failures: int = 0
    retried_successes: int = 0
    recent_failures: list[float] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        return self.failures / self.total_calls if self.total_calls else 0.0

    @property
    def retry_rate(self) -> float:
        return self.retried_successes / self.total_calls if self.total_calls else 0.0

    def record_attempt(self) -> None:
        self.total_attempts += 1

    def record_success(self, *, retried: bool) -> None:
        self.successes += 1
        if retried:
            self.retried_successes += 1

    def record_failure(self) -> None:
        self.failures += 1
        self.recent_failures.append(time.time())
        # 只留最近 10 分钟，避免无界增长
        cutoff = time.time() - 600
        self.recent_failures = [t for t in self.recent_failures if t >= cutoff]

    def reset(self) -> None:
        self.total_calls = 0
        self.total_attempts = 0
        self.successes = 0
        self.failures = 0
        self.retried_successes = 0
        self.recent_failures.clear()


stats = RetryStats()


async def with_retry(fn, *, policy: RetryPolicy | None = None, label: str = "llm"):
    """执行 fn，失败按指数退避重试；全部失败后抛出最后一次的 LLMError。

    调用方捕获 LLMError 后走统一降级话术。
    """
    p = policy or RetryPolicy()
    stats.total_calls += 1
    last_error: Exception | None = None

    for attempt in range(p.attempts):
        stats.record_attempt()
        try:
            result = await fn()
            stats.record_success(retried=attempt > 0)
            if attempt > 0:
                logger.info("%s 重试成功 attempt=%d", label, attempt)
            return result
        except LLMError as e:
            last_error = e
            logger.warning("%s 第 %d 次失败：%s", label, attempt + 1, e)
            if attempt < p.attempts - 1:
                await asyncio.sleep(p.delay_for(attempt))

    stats.record_failure()
    logger.error("%s 重试 %d 次后仍失败，进入降级", label, p.attempts)
    raise last_error  # type: ignore[misc]
