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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from app.agent.loop import AgentLoop  # noqa: E402
from app.agent.session import SessionStore  # noqa: E402
from app.agent.tools.client import MockServiceClient  # noqa: E402
from app.api import agent_routes  # noqa: E402
from app.config import settings  # noqa: E402
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
from app.store.memory import MemoryStore  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

CASES_DIR = ROOT / "eval" / "cases"
REPORT_DIR = ROOT / "eval" / "report"
BASELINE = ROOT / "eval" / "baseline" / "v1.json"

# turns 里用这个标记代替聊天文本，模拟前端地址表单提交
ADDRESS_FORM_TURN = "@address_form"


class InProcessMock(MockServiceClient):
    def __init__(self, app):
        # 不调 super().__init__ 就拿不到 timeout_ms / base_url，
        # 没被覆写的方法（如 create_urge）会当场 AttributeError
        super().__init__(base_url="http://mock")
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

    async def create_urge(self, order_id, *, request_id, trace_id):
        async with AsyncClient(transport=self._t, base_url="http://mock") as c:
            r = await c.post(
                f"/orders/{order_id}/urge",
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


async def build_shared(backend: str, model: str | None, embedder: str = "stub"):
    """worker 之间共享的部分：LLM 客户端与政策服务。

    两者都无每条用例的状态，且都不便宜——LLM 客户端复用连接，
    政策服务持有一份 embedding 模型（每次重建要重新加载权重）。
    """
    if backend == "real":
        inner = OpenAICompatLLMClient(model=model)
        llm = RecordingLLM(inner)
    else:
        from app.agent.llm.stub import StubLLMClient

        llm = StubLLMClient(classifier=_stub_classifier)

    # 政策服务必须显式装配，否则 POLICY 意图走不到检索，政策用例恒为 0
    policy = _build_eval_policy(llm, embedder)
    return llm, policy


def build_worker(llm, policy):
    """一个评测 worker 的独占状态：订单库、会话库、AgentLoop。

    并发跑用例时这些必须隔离——每条用例开跑前要把订单库和会话清回种子状态，
    共用一份的话，A 用例的清理会清掉 B 用例跑到一半的会话。
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    loop = AgentLoop(
        classifier=IntentClassifier(llm, keyword_map={}),
        sessions=SessionStore(redis, ttl_seconds=3600),
        tools=InProcessMock(_worker_app()),
        coordinator=HandoffCoordinator(HandoffQueue(redis)),
        policy=policy,
    )
    return _WorkerEnv(
        redis=redis,
        loop=loop,
        memory=MemoryStore(settings.seed_path, settings.refund_lock_shards),
    )


_APP = None


def _worker_app():
    """所有 worker 共用一个 app 实例：app 本身无状态，
    状态全在全局单例或 ContextVar 里，重复 create_app 只会重复挂载静态目录。
    """
    global _APP
    if _APP is None:
        _APP = create_app()
    return _APP


@dataclass
class _WorkerEnv:
    redis: Any
    loop: Any
    memory: Any


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
        # 并发时多个 worker 排队做 CPU 推理，30s 会把正常检索误判成超时；
        # 真实检索本身只要几百毫秒，放宽只影响"卡死"的上限。
        settings.rag_timeout_ms = 180_000
        try:
            import torch

            # N 个 worker 并发 encode，各自默认占满所有核会互相抢，
            # 单线程反而总吞吐更高。
            torch.set_num_threads(1)
        except ImportError:  # pragma: no cover - 未装 bge 时才没有 torch
            pass

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
        # 投诉/差评不再是独立意图（2026-11-13），归入 UNKNOWN 走安抚
        (("投诉", "差评", "骗子", "起诉", "举报", "12315"), Intent.UNKNOWN),
        # 换货：系统不支持，回固定话术。先排除"换地址/换收货地址"这类改地址表达。
        (("换个货", "换货", "换个尺码", "换尺码", "换个颜色", "换颜色", "换一件"), Intent.EXCHANGE),
        (("退款", "退货", "退钱", "退了", "退掉", "退我"), Intent.REFUND),
        (("物流", "快递", "包裹", "到哪", "发货"), Intent.LOGISTICS),
        (("地址",), Intent.CHANGE_ADDRESS),
        (("订单",), Intent.QUERY_ORDER),
    ]
    for words, intent in table:
        if any(w in text for w in words):
            return IntentResult(intent=intent, confidence=0.9)
    return IntentResult(intent=Intent.UNKNOWN, confidence=0.5)


async def run_case(client, mock_app, case: EvalCase, trace: str) -> dict:
    order_id = case.context.get("order_id") or "ORD1002"

    if case.path is EvalPath.E2E:
        # 逐轮记录：既查最终业务状态，也查关键轮次的交互（是否弹确认、是否转人工）。
        # 只查最终状态测不出"AI 有没有让用户确认"这类流程约束。
        transcript = []
        for turn in case.turns or []:
            if turn == ADDRESS_FORM_TURN:
                # 地址表单提交：走与前端表单同一条后端入口，不是发一句聊天文本。
                # 这样用例才真的覆盖"结构化地址 → 确认卡 → 写入"这段。
                from app.api.agent_routes import get_loop

                reply = await get_loop().submit_address(
                    session_id=f"eval-{case.id}",
                    user_id="u1",
                    address=case.expect.get("address") or {},
                    trace_id=trace,
                )
                transcript.append(
                    {
                        "state": reply.state,
                        "intent": reply.intent.value if reply.intent else None,
                        "reply": reply.reply,
                        "tool_calls": reply.tool_calls or [],
                    }
                )
                continue
            r = await client.post(
                "/agent/chat",
                json={"session_id": f"eval-{case.id}", "user_id": "u1", "message": turn, "order_id": order_id},
                headers={"X-Trace-Id": trace},
            )
            d = r.json()
            transcript.append(
                {
                    "state": d.get("state"),
                    "intent": d.get("intent"),
                    "reply": d.get("reply", ""),
                    "tool_calls": d.get("tool_calls") or [],
                }
            )
        order = (await client.get(f"/orders/{order_id}", headers={"X-Trace-Id": trace})).json()
        data = order.get("data") or {}
        return {
            "order_status": data.get("status"),
            "refund_count": 1 if data.get("status") == OrderStatus.REFUNDED.value else 0,
            # 业务结果不能只看状态：改地址失败时状态仍是 PAID，只有地址能证明有没有改成功
            "address": data.get("address"),
            "last_reply": transcript[-1]["reply"] if transcript else None,
            "turns": transcript,
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


async def run_one(env: _WorkerEnv, client, case: EvalCase, idx: int):
    """跑一条用例，并把 ContextVar 指向本 worker 的订单库与 loop。

    重置走 worker 私有的 memory/redis，不能用全局 reset_store 与 redis.flushall——
    那会把别的 worker 正在跑的用例状态一起清掉。
    """
    env.memory.reset()
    await env.redis.flushall()
    trace = f"{case.id}-{idx}".ljust(32, "0")[:32]
    with mock_routes.use_store(env.memory), agent_routes.use_loop(env.loop):
        try:
            actual = await run_case(client, _worker_app(), case, trace)
        except LLMError as e:
            actual = {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            actual = {"error": f"{type(e).__name__}: {e}"}
        return grade(case, actual)


async def main_async(args) -> int:
    cases = load_dir(CASES_DIR)
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c.path.value in wanted]
        if not cases:
            print(f"没有匹配的用例路径：{args.only}")
            return 1
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        cases = [c for c in cases if c.id in wanted]
        # 静默少跑比跑错更糟：ID 打错时应该立刻知道，而不是得到一个空报告
        missing = wanted - {c.id for c in cases}
        if missing:
            print(f"找不到这些用例 ID：{', '.join(sorted(missing))}")
            return 1
        if not cases:
            print("没有匹配的用例 ID")
            return 1
    if args.limit:
        cases = cases[: args.limit]

    llm, policy = await build_shared(args.llm, args.model, args.embedder)
    concurrency = max(1, args.concurrency)
    workers = [build_worker(llm, policy) for _ in range(min(concurrency, len(cases)))]
    # 共享一个 client：ASGITransport 是进程内直连，连接池无意义，并发 post 本身安全
    client = AsyncClient(transport=ASGITransport(app=_worker_app()), base_url="http://agent")

    queue: asyncio.Queue = asyncio.Queue()
    for i, case in enumerate(cases):
        queue.put_nowait((i, case))
    results: list = [None] * len(cases)

    async def drain(env: _WorkerEnv):
        while True:
            try:
                idx, case = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            results[idx] = await run_one(env, client, case, idx)

    started = time.perf_counter()
    try:
        await asyncio.gather(*(drain(w) for w in workers))
    finally:
        await client.aclose()
        inner = getattr(llm, "inner", None)
        if inner is not None and hasattr(inner, "aclose"):
            await inner.aclose()
        for w in workers:
            await w.redis.aclose()

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
        if args.llm != "real":
            print(
                "\n⚠ 桩模式不能写基线：桩的准确率没有意义，基线只来自真实模型。已跳过。"
            )
        else:
            save_baseline(BASELINE, report)

    from app.eval.html_report import write_html

    stamp = Path(js).stem.replace("report-", "")
    page = write_html(report, REPORT_DIR / f"report-{stamp}.html")
    latest = write_html(report, REPORT_DIR / "latest.html")

    print(report_mod.render_markdown(report))
    print(f"\n报告：{md}\n      {js}\n      {page}\n最新：{latest}")

    if not args.no_open:
        _open_in_browser(latest)

    if isinstance(llm, RecordingLLM):
        print(f"LLM 调用 {llm.calls} 次，累计 {llm.seconds:.1f}s")

    return 1 if report.blocked else 0


def _open_in_browser(path: Path) -> None:
    """打开本地 HTML。Windows 上 webbrowser.open 常被默认程序设置吃掉，
    优先用 Start-Process，失败再退回 webbrowser。"""
    import subprocess
    import sys
    import webbrowser

    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", f"Start-Process '{path.resolve()}'"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            webbrowser.open(path.resolve().as_uri())
    except Exception:  # noqa: BLE001 - 打不开不影响评测结果
        try:
            webbrowser.open(path.resolve().as_uri())
        except Exception:  # noqa: BLE001
            pass


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
    p.add_argument(
        "--only",
        default=None,
        help="只跑指定路径，逗号分隔：e2e,policy,clarify,handoff,irrelevant,business_action",
    )
    p.add_argument(
        "--ids",
        default=None,
        help="只跑指定用例 ID，逗号分隔。用于复现某几条失败样本，配 --concurrency=4 秒级出结果",
    )
    p.add_argument(
        "--no-open",
        action="store_true",
        help="生成 HTML 报告但不自动打开浏览器（CI 用）",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="并发 worker 数。1 为串行；LLM 网关实测饱和点约 16，建议 12",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只跑前 N 条，用于小批验证并行与串行结果是否一致",
    )
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--no-baseline", action="store_true")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
