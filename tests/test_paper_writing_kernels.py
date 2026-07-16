"""lit-survey 与 peer-review skill 的确定性 kernel 函数测试。

这两个 skill 的核心是确定性逻辑 (LQS 评分 / reviewer 聚合 / 防通胀),
不依赖 LLM 或网络 — 本测试确保这些"游戏规则"正确且可复现。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SKILLS = Path(__file__).resolve().parent.parent / "skills" / "skills"


def _load_kernel(name: str):
    """从 skill 目录加载 kernel.py (不经 host sidecar 注入, 直接 import)。"""
    p = _SKILLS / name / "kernel.py"
    spec = importlib.util.spec_from_file_location(f"{name}_kernel", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{name}_kernel"] = mod
    spec.loader.exec_module(mod)
    return mod


lit = _load_kernel("lit-survey")
rev = _load_kernel("peer-review")


# ---------- LQS 评分 ----------


class TestLQSScore:
    def test_top_tier_recent_paper_scores_high(self):
        """顶会新作应得高分 (must-cite)。"""
        r = lit.lqs_score({
            "year": 2026, "citations": 800, "venue": "NeurIPS",
            "authors": list(range(10)), "status": "accepted",
        })
        assert r["lqs"] >= 8.0
        assert r["tier"] == "must"

    def test_old_preprint_scores_low(self):
        """老 preprint 应被 drop。"""
        r = lit.lqs_score({
            "year": 2019, "citations": 5, "venue": "arXiv",
            "authors": ["x"], "status": "preprint",
        })
        assert r["lqs"] < 5.0
        assert r["tier"] == "drop"

    def test_conditional_middle_ground(self):
        """中等质量论文落在 conditional 区间。"""
        r = lit.lqs_score({
            "year": 2024, "citations": 40, "venue": "ACL Workshop",
            "authors": ["x", "y"], "status": "accepted",
        })
        assert 5.0 <= r["lqs"] < 7.0
        assert r["tier"] == "conditional"

    def test_missing_fields_dont_crash(self):
        """缺字段的论文不崩, 给中性低分。"""
        r = lit.lqs_score({})
        assert r["lqs"] > 0
        assert "dimensions" in r

    def test_lqs_is_deterministic(self):
        """同一输入两次评分结果相同 (可复现)。"""
        p = {
            "year": 2025, "citations": 100, "venue": "ICML",
            "authors": ["a"], "status": "accepted",
        }
        assert lit.lqs_score(p) == lit.lqs_score(p)

    def test_score_papers_stamps_lqs(self):
        """批量评分给每篇盖 lqs/tier 戳。"""
        out = lit.score_papers([
            {"year": 2026, "venue": "NeurIPS", "status": "accepted"},
            {"year": 2018, "venue": "arXiv", "status": "preprint"},
        ])
        assert "lqs" in out[0] and "lqs_tier" in out[0]
        assert out[0]["lqs_tier"] == "must"
        assert out[1]["lqs_tier"] == "drop"

    def test_venue_workshop_scores_low_even_if_top_venue(self):
        """顶 venue + workshop 仍是 workshop 分 (4分)。"""
        assert lit.score_venue("NeurIPS Workshop") == 4.0


class TestClassifyCitation:
    def test_must_with_long_context_is_A(self):
        """must-cite + 长讨论 → A 级。"""
        paper = {"lqs_tier": "must"}
        assert lit.classify_citation(paper, context="x" * 500) == "A"

    def test_must_short_context_is_B(self):
        """must-cite + 短引用 → B 级。"""
        paper = {"lqs_tier": "must"}
        assert lit.classify_citation(paper, context="see") == "B"

    def test_conditional_is_C(self):
        """conditional → C 级。"""
        paper = {"lqs_tier": "conditional"}
        assert lit.classify_citation(paper) == "C"

    def test_drop_is_D(self):
        """drop → D 级 (不引用)。"""
        paper = {"lqs_tier": "drop"}
        assert lit.classify_citation(paper) == "D"


class TestBibHealth:
    def test_healthy_bib(self):
        """达标 bibliography 报 ok。"""
        bib = """
        @article{a, title={A}, author={X}, year={2026}, journal={NeurIPS}}
        @article{b, title={B}, author={Y}, year={2026}, journal={ICML}}
        """
        h = lit.bib_health(bib)
        assert h["total"] == 2
        assert h["ok"] is True

    def test_arxiv_heavy_flagged(self):
        """arXiv 占比超 60% 被标记。"""
        bib = """
        @article{a, title={A}, author={X}, year={2026}, journal={NeurIPS}}
        @article{b, title={B}, author={Y}, year={2026}, journal={arXiv}}
        @article{c, title={C}, author={Z}, year={2026}, journal={arXiv}}
        @article{d, title={D}, author={W}, year={2026}, journal={arXiv}}
        """
        h = lit.bib_health(bib)
        assert h["arxiv_only_ratio"] > 0.60
        assert any("arxiv" in i for i in h["issues"])

    def test_empty_bib(self):
        """空 bibliography 报 not ok。"""
        h = lit.bib_health("")
        assert h["ok"] is False


# ---------- peer-review 聚合 ----------


class TestAggregateReviews:
    def test_median_over_reviewers(self):
        """最终分 = reviewer 总分的中位数 (非均值, 抗离群)。"""
        reviews = [
            {
                "reviewer": "R1",
                "scores": {"novelty": 9, "clarity": 9, "technical_depth": 9,
                           "comprehensiveness": 9, "experimental": 9},
                "recommendation": "accept",
            },
            {
                "reviewer": "R2",
                "scores": {"novelty": 5, "clarity": 5, "technical_depth": 5,
                           "comprehensiveness": 5, "experimental": 5},
                "recommendation": "borderline",
            },
            {
                "reviewer": "R3",
                "scores": {"novelty": 7, "clarity": 7, "technical_depth": 7,
                           "comprehensiveness": 7, "experimental": 7},
                "recommendation": "weak accept",
            },
        ]
        agg = rev.aggregate_reviews(reviews)
        # 中位数 review overall = 7.0 (9,5,7 → median 7)
        assert agg["score"] == 7.0

    def test_merges_weaknesses(self):
        """聚合所有 reviewer 的 weakness。"""
        reviews = [
            {"reviewer": "R1", "scores": {"novelty": 7}, "weaknesses": [{"text": "w1"}]},
            {
                "reviewer": "R2", "scores": {"novelty": 7},
                "weaknesses": [{"text": "w2"}, {"text": "w3"}],
            },
        ]
        agg = rev.aggregate_reviews(reviews)
        assert len(agg["weaknesses"]) == 3

    def test_empty_reviews(self):
        """空 review 列表不崩。"""
        agg = rev.aggregate_reviews([])
        assert agg["ok"] is False


class TestAntiInflation:
    def test_first_round_capped_at_7(self):
        """首轮分数封顶 7.0。"""
        r = rev.apply_anti_inflation(9.5, round_num=1)
        assert r["score"] == 7.0
        assert r["capped"] is True

    def test_later_round_not_first_capped(self):
        """非首轮不受首轮封顶限制。"""
        r = rev.apply_anti_inflation(8.0, round_num=3, prev_score=7.0)
        assert r["score"] == 8.0  # delta 1.0 <= 1.5, 不 cap

    def test_max_delta_enforced(self):
        """单轮涨幅超 1.5 被 cap。"""
        # 传一个未解决 weakness, 避免触发"全解决可疑"cap, 只测 max delta
        r = rev.apply_anti_inflation(
            9.0, round_num=2, prev_score=7.0,
            weaknesses=[{"severity": "major", "text": "open"}],
        )
        # delta 2.0 > 1.5 → capped to 7.0 + 1.5 = 8.5
        assert r["score"] == 8.5
        assert r["capped"] is True

    def test_all_resolved_at_high_score_flagged(self):
        """高分 + 无未解决 weakness 被标记 (可疑)。"""
        r = rev.apply_anti_inflation(9.0, round_num=2, prev_score=8.0,
                                     weaknesses=[{"severity": "resolved"}])
        # 无 open weakness + 高分 → cap 到 8.4
        assert r["score"] <= 8.4


class TestRouteWeaknesses:
    def test_citation_routes_to_lit_survey(self):
        routed = rev.route_weaknesses([{"text": "citation coverage insufficient"}])
        assert routed[0]["route_to"] == "lit-survey"

    def test_taxonomy_routes_to_paper_structure(self):
        routed = rev.route_weaknesses([{"text": "taxonomy not MECE"}])
        assert routed[0]["route_to"] == "paper-structure"

    def test_table_routes_to_figures(self):
        routed = rev.route_weaknesses([{"text": "table unclear"}])
        assert routed[0]["route_to"] == "academic-figures"

    def test_unknown_defaults_to_paper_structure(self):
        """无法匹配的弱点默认到 paper-structure (通用修订)。"""
        routed = rev.route_weaknesses([{"text": "overall flow is weird"}])
        assert routed[0]["route_to"] == "paper-structure"


class TestShouldStop:
    def test_target_reached(self):
        stop, why = rev.should_stop(8.6, round_num=5)
        assert stop is True
        assert "target" in why

    def test_max_rounds_exceeded(self):
        stop, why = rev.should_stop(7.0, round_num=13)
        assert stop is True
        assert "max rounds" in why

    def test_plateau(self):
        stop, why = rev.should_stop(7.1, round_num=5, prev_score=7.0)
        # delta 0.1 <= 0.3 → plateau
        assert stop is True
        assert "plateau" in why.lower()

    def test_continue(self):
        stop, _ = rev.should_stop(7.5, round_num=3, prev_score=6.5)
        # delta 1.0 > 0.3, 未达 8.5, 未超轮 → 继续
        assert stop is False


class TestRegressionCheck:
    def test_no_regression(self):
        prev = [{"text": "old weakness"}]
        curr = [{"text": "new weakness"}]
        r = rev.regression_check(curr, prev)
        assert r["ok"] is True
        assert r["newly_resolved"] == 1

    def test_regression_detected(self):
        prev = [{"text": "bad taxonomy", "severity": "major"}]
        curr = [{"text": "bad taxonomy", "severity": "major"}]
        r = rev.regression_check(curr, prev)
        assert r["regressions"] == 1
        assert r["ok"] is False
