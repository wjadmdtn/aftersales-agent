"""T027: 标定脚本的判定与扫阈逻辑。

标定本身依赖真实 embedder，不进 CI；但「给定相似度 → 选阈值」是纯逻辑，
这部分必须可测——否则标定出来的数字没人能核。
"""
from __future__ import annotations

import pytest

from app.policy.calibrate import (
    Metrics,
    Sample,
    _classify,
    _evaluate,
    _write_env_updates,
    sweep,
    sweep_margin,
)
from app.policy.models import RetrievalDecision


def _pos(top1, top2, expected="A", top1_clause="A"):
    s = Sample(query="q", category="positive", expected_clause=expected)
    s.top1, s.top2, s.top1_clause = top1, top2, top1_clause
    return s


def _neg(top1, top2=None, category="off_topic"):
    s = Sample(query="q", category=category)
    s.top1, s.top2 = top1, top2
    return s


def test_classify_miss_when_below_threshold():
    assert _classify(_pos(0.59, 0.1), 0.6, 0.05) is RetrievalDecision.MISS


def test_classify_ambiguous_when_gap_below_margin():
    assert _classify(_pos(0.9, 0.88), 0.6, 0.05) is RetrievalDecision.AMBIGUOUS


def test_classify_hit_when_gap_clears_margin():
    assert _classify(_pos(0.9, 0.7), 0.6, 0.05) is RetrievalDecision.HIT


def test_classify_single_candidate_is_hit_not_ambiguous():
    """只有一个候选时没有 top2，不应因为「分差」判歧义。"""
    assert _classify(_pos(0.9, None), 0.6, 0.05) is RetrievalDecision.HIT


def test_classify_no_candidate_is_miss():
    assert _classify(_neg(None), 0.6, 0.05) is RetrievalDecision.MISS


class TestEvaluate:
    def test_wrong_top1_counts_as_fn_not_tp(self):
        """召回对了分数但条款错了，是错答不是漏答，两者都不能算 tp。"""
        m = _evaluate([_pos(0.9, 0.1, expected="A", top1_clause="B")], [], 0.6, 0.05)
        assert (m.tp, m.wrong_top1, m.fn) == (0, 1, 1)

    def test_fpr_excludes_ambiguous_negatives(self):
        """负样本被追问不算 FPR——它不触发生成。"""
        m = _evaluate([], [_neg(0.9, 0.88), _neg(0.1)], 0.6, 0.05)
        assert m.fp == 0
        assert m.ambiguous_neg == 1
        assert m.fpr == 0.0

    def test_fpr_counts_only_hit_negatives(self):
        negs = [_neg(0.9), _neg(0.1)]
        m = _evaluate([], negs, 0.6, 0.05)
        assert m.fp == 1
        # 分母只含负样本：1 误答 / 2 条负样本
        assert m.fpr == 0.5

    def test_fpr_denominator_excludes_positives(self):
        """FPR 不该被正样本漏答稀释——那是 recall 的事。"""
        pos = [_pos(0.1, 0.05) for _ in range(50)]  # 全部漏答
        neg = [_neg(0.9), _neg(0.1)]
        m = _evaluate(pos, neg, 0.6, 0.05)
        assert m.fpr == 0.5

    def test_action_negatives_excluded_from_fpr(self):
        """action 类走不到检索，不该算进检索层 FPR。"""
        negs = [_neg(0.95, category="action"), _neg(0.1)]
        m = _evaluate([], negs, 0.6, 0.05)
        assert m.fp == 0
        assert m.action_leak == 1
        assert m.action_leak_rate == 1.0

    def test_precision_and_recall_are_zero_safe(self):
        m = Metrics(threshold=0.6, margin=0.0)
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.fpr == 0.0


class TestSweep:
    def test_picks_most_correct_answers_within_budget(self):
        pos = [_pos(0.9, 0.1) for _ in range(9)] + [_pos(0.4, 0.1)]
        neg = [_neg(0.1) for _ in range(20)] + [_neg(0.7)]
        _, best = sweep(pos, neg, fpr_budget=0.05, amb_budget=0.15)
        # 预算 5% 允许 1 条负样本误答。阈值 0.2~0.70 都能达到 tp=10 且只误答 1 条，
        # 同 tp 时取最低阈值（覆盖最广）
        assert best is not None
        assert best.tp == 10
        assert best.fp == 1
        # 唯一必须守住的：阈值不能高到把能答对的样本切掉
        assert best.threshold <= 0.40

    def test_lowering_threshold_below_negative_breaks_fpr(self):
        """负样本 top1=0.7 时，阈值 ≤ 0.70 会让它被判 hit，FPR 超预算。"""
        pos = [_pos(0.9, 0.1)]
        neg = [_neg(0.7), _neg(0.1)]
        _, best = sweep(pos, neg, fpr_budget=0.05, amb_budget=0.15)
        assert best is not None
        assert best.threshold > 0.70

    def test_margin_can_rescue_fpr_by_downgrading_to_ambiguous(self):
        """负样本 top1 高但 top2 贴得近时，大 margin 能把它推成 ambiguous，FPR 反而达标。

        这正是「阈值与 margin 必须联合搜索」的理由：固定 margin=0 会误判为不可救。
        """
        pos = [_pos(0.9, 0.1)]
        neg = [_neg(0.85, 0.84)]  # gap = 0.01
        _, best = sweep(pos, neg, fpr_budget=0.0, amb_budget=0.15)
        assert best is not None
        assert best.margin > 0.01

    def test_margin_is_not_allowed_to_paper_over_ambiguous_negatives(self):
        """margin 把绝大多数负样本推成 ambiguous 时，追问代价要计入预算。

        防的是：低阈值 + 大 margin 把 FPR 刷成 0，代价是用户被大量无意义追问。
        amb_budget 约束的是正样本追问率，负样本追问另由 neg_ambiguous_budget 兜住。
        """
        pos = [_pos(0.9, 0.1) for _ in range(20)]
        # 5 条负样本各自 top1 高、top2 紧贴某条固定值 → 大 margin 全推成 ambiguous
        neg = [_neg(0.90, 0.88) for _ in range(5)]
        _, best = sweep(
            pos, neg, fpr_budget=0.0, amb_budget=0.15, neg_amb_budget=0.5
        )
        assert best is not None
        assert best.ambiguous_neg / (best.ambiguous_neg + best.neg_correct_reject) <= 0.5

    def test_returns_none_when_no_threshold_satisfies_budget(self):
        pos = [_pos(0.3, 0.1)]
        neg = [_neg(0.95, 0.1)]
        _, best = sweep(pos, neg, fpr_budget=0.0, amb_budget=0.0)
        assert best is None

    def test_fixed_margin_restricts_search(self):
        pos = [_pos(0.9, 0.89)]
        neg = [_neg(0.1)]
        rows, _ = sweep(pos, neg, fpr_budget=0.05, amb_budget=1.0, fixed_margin=0.0)
        assert {r.margin for r in rows} == {0.0}

    def test_ambiguous_positives_violate_amb_budget(self):
        """正样本 top1/top2 贴太近时，margin=0 下仍应能命中；约束不该误伤这里。"""
        pos = [_pos(0.9, 0.89)]
        neg = [_neg(0.1)]
        _, best = sweep(pos, neg, fpr_budget=0.05, amb_budget=0.0)
        assert best is not None


class TestSweepMargin:
    def test_returns_first_margin_that_exceeds_budget(self):
        pos = [_pos(0.9, 0.1), _pos(0.9, 0.85)]
        rows, chosen = sweep_margin(pos, [], threshold=0.6, amb_budget=0.15)
        # 0.85 的正样本 gap=0.05，margin 超过 0.05 后会被追问 → 追问率 50%，超预算
        assert chosen <= 0.05
        assert any(r.margin == chosen for r in rows)


def test_write_env_updates_only_threshold_lines(tmp_path):
    env = tmp_path / ".env.example"
    env.write_text(
        "MOCK_RAG_SIMILARITY_THRESHOLD=0.6\nMOCK_RAG_AMBIGUITY_MARGIN=0.05\nOTHER=1\n",
        encoding="utf-8",
    )
    _write_env_updates(env, 0.72, 0.03)
    text = env.read_text(encoding="utf-8")
    assert "MOCK_RAG_SIMILARITY_THRESHOLD=0.72" in text
    assert "MOCK_RAG_AMBIGUITY_MARGIN=0.03" in text
    assert "OTHER=1" in text
