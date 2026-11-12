"""评测主程序。

    python -m eval.runner --llm=stub              # CI，确定性
    python -m eval.runner --llm=real --model=qwen-plus   # 出效果数字

stub 与 real 共用同一套用例、判分和报告，只有 LLM 客户端不同。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
# 种子路径是相对 backend/ 的，切过去再跑
os.chdir(BACKEND)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import fakeredis.aioredis  # noqa: E402

import app.api.routes as mock_routes  # noqa: E402
from app.agent.intent import IntentClassifier  # noqa: E402
from app.agent.llm.base import LLMError  # noqa: E402
from app.agent.llm.openai_compat import OpenAICompatLLMClient  # noqa: E402
from app.agent.session import SessionStore  # noqa: E402
from app.agent.tools.client import MockServiceClient  # noqa: E402
from app.api import agent_routes  # noqa: E402
from app.eval import report as report_mod  # noqa: E402
from app.eval.baseline import apply_comparison, load_baseline, save_baseline  # noqa: E402
from app.eval.grader import grade  # noqa: E402
from app.eval.loader import load_dir  # noqa: E402
from app.eval.metrics import compute_metrics  # noqa: E402
from app.eval.schema import EvalCase, EvalPath, EvalReport  # noqa: E402
from app.handoff.coordinator import HandoffCoordinator  # noqa: E402
from app.handoff.queue import HandoffQueue  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.agent import Intent, IntentResult  # noqa: E402
from app.models.enums import OrderStatus, ResultCode  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

CASES_DIR = ROOT / "eval" / "cases"
REPORT_DIR = ROOT / "eval" / "report"
BASELINE = ROOT / "eval" / "baseline" / "v1.json"


class InProcessMock(MockServiceClient):
    def __init__(self, app):
        self._t = ASGITransport(app=app)

    async def _get(self, path: str, *, trace_id: str):
        async with AsyncClient(transport=self._t, base_url="http://mock") as c:
            r = await c.get(path, headers={"X-Trace-Id": trace_id})
            return r.json()

    async def create_refund(self, order_id, amount, *, request_id, trace_id):
        async with AsyncClient(transport=self._t, base_url="http://mock") as c:
            r = await c.post(
                f"/orders/{order_id}/refund",
                json={"amount": str(amount)},
                headers={"X-Trace-Id": trace_id, "X-Request-Id": request_id},
            )
            return r.json()

    async def update_address(self, order_id, address, *, trace_id, request_id=None):
        async with AsyncClient(transport=self._t, base_url="http://mock") as c:
            headers = {"X-Trace-Id": trace_id}
            if request_id:
                headers["X-Request-Id"] = request_id
            r = await c.put(f"/orders/{order_id}/address", json=address, headers=headers)
            return r.json()


class RecordingLLM:
    """包装真实客户端，记录调用次数与耗时。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0
        self.seconds = 0.0

    async def classify(self, *, user_input, hint_keywords, context):
        start = time.perf_counter()
        try:
            return await self.inner.classify(
                user_input=user_input, hint_keywords=hint_keywords, context=context
            )
        finally:
            self.calls += 1
            self.seconds += time.perf_counter() - start

    async def answer_policy(self, *, user_input, retrieval):
        """政策问答也要代理。漏了这个，政策链路会静默降级为 miss。"""
        start = time.perf_counter()
        try:
            return await self.inner.answer_policy(user_input=user_input, retrieval=retrieval)
        finally:
            self.calls += 1
            self.seconds += time.perf_counter() - start

    async def rerank_policy(self, *, query, clauses):
        """必须透传：装配层靠 hasattr(llm, "rerank_policy") 决定是否启用 rerank，
        包装类漏了它，评测就跑在没 rerank 的链路上，回调不出真实效果。"""
        inner = getattr(self.inner, "rerank_policy", None)
        if inner is None:
            raise RuntimeError("inner llm has no rerank_policy")
        start = time.perf_counter()
        try:
            return await inner(query=query, clauses=clauses)
        finally:
            self.calls += 1
            self.seconds += time.perf_counter() - start


async def build_env(backend: str, model: str | None, embedder: str = "stub"):
    mock_routes.reset_store()
    mock_app = create_app()

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = SessionStore(redis, ttl_seconds=3600)
    tools = InProcessMock(mock_app)

    if backend == "real":
        inner = OpenAICompatLLMClient(model=model)
        llm = RecordingLLM(inner)
    else:
        from app.agent.llm.stub import StubLLMClient

        llm = StubLLMClient(classifier=_stub_classifier)

    # 政策服务必须显式装配，否则 POLICY 意图走不到检索，政策用例恒为 0
    policy = _build_eval_policy(llm, embedder)

    classifier = IntentClassifier(llm, keyword_map={})
    coordinator = HandoffCoordinator(HandoffQueue(redis))
    agent_routes.configure_agent(
        classifier=classifier,
        sessions=store,
        tools=tools,
        coordinator=coordinator,
        policy=policy,
    )

    agent_app = create_app()
    client = AsyncClient(transport=ASGITransport(app=agent_app), base_url="http://agent")
    return client, mock_app, redis, llm, store


def _build_eval_policy(llm, embedder: str):
    """政策装配走 build_policy_service（与生产同一路径）。

    此前这里手搓 Retriever + PolicyService，没接关键词召回与 rerank——
    评测测的是 005 的老链路，后面加的改进在上面零覆盖。
    """
    from app.config import settings

    if embedder == "stub":
        # 桩向量尺度在 0.0~0.1，用真实阈值会全程 miss。这里放行 top1，只测链路。
        # rerank 也关掉：桩模型没有 rerank_policy，开着反而崩。
        settings.rag_similarity_threshold = 0.0
        settings.rag_ambiguity_margin = 0.0
        settings.rag_rerank_enabled = False
    else:
        settings.rag_similarity_threshold = 0.64
        settings.rag_ambiguity_margin = 0.0

    settings.rag_embedder = embedder

    from app.services.policy import build_policy_service

    return build_policy_service(llm)


def _stub_classifier(text: str) -> IntentResult:
    """桩：按关键词返回。仅用于验证链路，准确率不代表真实效果。

    顺序即优先级：先判「问规则」的政策特征，再判动作。
    "退款多久能到账" 含「退款」但问的是规则，必须先命中 policy。
    """
    policy_markers = (
        "多久", "几天", "怎么算", "谁出", "谁承担", "范围", "哪些", "标准", "政策",
        "规则", "条件", "时限", "时效", "能退吗", "能不能退", "可以退吗", "能开",
        "能换", "保修", "价保", "七天无理由", "定金", "以旧换新", "凭证",
        "赔偿", "赔付", "退不退", "算不算", "属于", "怎么样算", "不符合",
    )
    if any(m in text for m in policy_markers):
        return IntentResult(intent=Intent.POLICY, confidence=0.9)

    table = [
        (("转人工", "人工", "真人", "客服电话"), Intent.MANUAL),
        (("投诉", "差评", "骗子", "起诉", "举报", "12315"), Intent.COMPLAINT),
        (("退款", "退货", "退钱", "退了", "退掉", "退我"), Intent.REFUND),
        (("物流", "快递", "包裹", "到哪", "发货"), Intent.LOGISTICS),
        (("地址",), Intent.CHANGE_ADDRESS),
        (("订单",), Intent.QUERY_ORDER),
    ]
    for words, intent in table:
        if any(w in text for w in words):
            return IntentResult(intent=intent, confidence=0.9)
    return IntentResult(intent=Intent.GENERAL, confidence=0.5)


async def run_case(client, mock_app, case: EvalCase, trace: str) -> dict:
    order_id = case.context.get("order_id") or "ORD1002"

    if case.path is EvalPath.E2E:
        last = None
        for turn in case.turns or []:
            r = await client.post(
                "/agent/chat",
                json={"session_id": f"eval-{case.id}", "user_id": "u1", "message": turn, "order_id": order_id},
                headers={"X-Trace-Id": trace},
            )
            last = r.json()
        order = (await client.get(f"/orders/{order_id}", headers={"X-Trace-Id": trace})).json()
        return {
            "order_status": (order.get("data") or {}).get("status"),
            "refund_count": 1 if (order.get("data") or {}).get("status") == OrderStatus.REFUNDED.value else 0,
            "last_reply": (last or {}).get("reply"),
        }

    # 系统异常 / 边界外的信号通过 context 传入，走 coordinator 直接判定
    if case.path is EvalPath.HANDOFF and (
        "tool_failure_streak" in case.context or "last_tool_status" in case.context
    ):
        from app.handoff.triggers import TurnSignals, evaluate
        from app.models.handoff import ToolStatus

        sig = TurnSignals(
            user_message=case.input or "",
            tool_failure_streak=case.context.get("tool_failure_streak", 0),
            last_tool_status=ToolStatus(case.context["last_tool_status"])
            if "last_tool_status" in case.context
            else None,
        )
        tr = evaluate(sig)
        if not tr.triggered:
            return {"state": "none", "triggered": False}
        from app.handoff.priority import calc_priority
        from app.handoff.emotion import detect_emotion

        prio, _ = calc_priority(
            trigger_type=tr.trigger_type, emotion=detect_emotion(case.input or "")
        )
        return {
            "state": "handoff_queued",
            "triggered": True,
            "trigger_type": tr.trigger_type.value,
            "priority": prio.value,
        }

    r = await client.post(
        "/agent/chat",
        json={
            "session_id": f"eval-{case.id}",
            "user_id": "u1",
            "message": case.input,
            "order_id": order_id,
        },
        headers={"X-Trace-Id": trace},
    )
    data = r.json()
    if case.path is EvalPath.POLICY:
        from app.api.agent_routes import get_loop

        loop = get_loop()
        sess = await loop.sessions.get(f"eval-{case.id}")
        cited = list(getattr(sess, "last_cited_clause_ids", []) or []) if sess else []
        return {
            "intent": data.get("intent"),
            "state": data.get("state"),
            "reply": data.get("reply", ""),
            "cited_clause_ids": cited,
        }

    out = {
        "intent": data.get("intent"),
        "mode": data.get("mode"),
        "state": data.get("state"),
        "reply": data.get("reply", ""),
        "triggered": data.get("state") == "handoff_queued",
    }

    # 转人工类需要 trigger_type / priority，从队列里读
    if case.path is EvalPath.HANDOFF and data.get("state") == "handoff_queued":
        from app.api.agent_routes import get_loop

        q = get_loop().coordinator.queue
        pack = await q.get_pack(f"eval-{case.id}") if q else None
        if pack:
            out["trigger_type"] = pack.trigger_type.value
            out["priority"] = pack.priority.value
    return out


async def main_async(args) -> int:
    cases = load_dir(CASES_DIR)
    client, mock_app, redis, llm, store = await build_env(args.llm, args.model, args.embedder)

    results = []
    started = time.perf_counter()
    try:
        for i, case in enumerate(cases, 1):
            # 每条用例前重置状态，避免互相污染（FR-022）
            mock_routes.reset_store()
            await redis.flushall()
            trace = f"{case.id}-{i}".ljust(32, "0")[:32]
            try:
                actual = await run_case(client, mock_app, case, trace)
            except LLMError as e:
                actual = {"error": str(e)}
            except Exception as e:  # noqa: BLE001
                actual = {"error": f"{type(e).__name__}: {e}"}
            results.append(grade(case, actual))
    finally:
        await client.aclose()
        await redis.aclose()

    elapsed = time.perf_counter() - started
    report = EvalReport(
        backend=args.llm,
        model=args.model or ("qwen-plus" if args.llm == "real" else None),
        case_count=len(cases),
        elapsed_seconds=round(elapsed, 2),
        metrics=compute_metrics(results),
        results=results,
    )

    if BASELINE.exists() and not args.no_baseline:
        apply_comparison(report, load_baseline(BASELINE))

    md, js = report_mod.write_report(report, REPORT_DIR)
    if args.update_baseline:
        save_baseline(BASELINE, report)

    print(report_mod.render_markdown(report))
    print(f"\n报告：{md}\n      {js}")

    if isinstance(llm, RecordingLLM):
        print(f"LLM 调用 {llm.calls} 次，累计 {llm.seconds:.1f}s")

    return 1 if report.blocked else 0


def main() -> int:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(logging.WARNING)

    p = argparse.ArgumentParser()
    p.add_argument("--llm", choices=["stub", "real"], default="stub")
    p.add_argument("--model", default=None)
    p.add_argument(
        "--embedder",
        choices=["stub", "bge"],
        default="stub",
        help="bge 才真正测检索质量；stub 只验证接线，政策用例数字不可对外引用",
    )
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--no-baseline", action="store_true")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
