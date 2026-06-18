"""Projection helpers for Hermes /supergoal mission control.

This module is deliberately independent from ``goals.py``'s GoalState dataclass.
It extracts structured observation events from visible assistant output so the
runtime can rebuild useful ledgers even when the strategic critic times out.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

_ARTIFACT_PATH_RE = re.compile(
    r"(?:(?:^|\s)(?:[./~][\w./-]+|[\w.-]+/[\w./-]+)\.(?:py|ts|tsx|js|json|md|txt|csv|log|html|yaml|yml|png|jpg|pdf))",
    re.IGNORECASE,
)


def truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "… [truncated]"


def classify_action_text(text: str) -> str:
    """Classify a supergoal turn's dominant action class."""
    t = (text or "").lower()
    if any(k in t for k in ["no-edge", "no edge", "归因", "report", "总结", "报告"]):
        return "reporting"
    if any(k in t for k in ["hypothesis", "hypotheses", "假设", "portfolio", "策略候选"]):
        return "hypothesis_generation"
    if any(k in t for k in ["experiment", "backtest", "验证策略", "baseline", "ledger", "acceptance", "run test"]):
        return "experiment_execution"
    if any(k in t for k in ["verify", "test", "pytest", "lint", "validate", "验证", "artifact"]):
        return "validation"
    if any(k in t for k in ["search", "survey", "paper", "github", "docs", "web", "研究", "调研", "检索"]):
        return "research"
    if any(k in t for k in ["validator", "checker", "audit", "infra", "pipeline", "framework", "tool", "script", "模块", "平台", "基础设施"]):
        return "infra_engineering"
    if any(k in t for k in ["permission", "scope", "policy", "destructive", "安全"]):
        return "safety"
    return "unknown"


def artifact_paths(text: str) -> List[str]:
    """Return unique artifact-like paths mentioned in text."""
    paths: List[str] = []
    for match in _ARTIFACT_PATH_RE.finditer(text or ""):
        path = match.group(0).strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def extract_observation_events(last_response: str) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Extract structured observation events from an assistant response.

    Returns ``(event_type, summary, data)`` tuples suitable for
    ``append_goal_event``.  Extraction is conservative and evidence-gated: prose
    like "I found one paper" does not become tool-backed research unless the
    response also names an artifact or verification signal.
    """
    normalized = " ".join((last_response or "").split())
    if not normalized:
        return []
    low = normalized.lower()
    events: List[Tuple[str, str, Dict[str, Any]]] = []

    action = classify_action_text(normalized)
    if action != "unknown":
        events.append(("action_class_observed", action, {"action_class": action}))

    paths = artifact_paths(normalized)
    for path in paths[:5]:
        events.append(("artifact_observed", path, {"artifact_path": path, "locator": path}))

    has_verification = any(k in low for k in ("verified", "tested", "pytest", "test passed", "tests passed", "验证", "测试"))
    if has_verification:
        events.append(("verification_observed", truncate(normalized, 180), {"evidence": truncate(normalized, 240)}))

    has_research_language = any(k in low for k in ("research", "survey", "github", "docs", "paper", "benchmark", "external", "news", "rss", "调研", "外部", "新闻"))
    has_observation_evidence = bool(paths) or has_verification
    if has_observation_evidence and has_research_language:
        source_type = "benchmark" if any(k in low for k in ("benchmark", "baseline", "backtest", "基准", "回测")) else "local"
        if any(k in low for k in ("github", "repo")):
            source_type = "github"
        elif any(k in low for k in ("paper", "arxiv")):
            source_type = "paper"
        elif any(k in low for k in ("docs", "web", "rss", "news", "external", "新闻", "外部")):
            source_type = "web"
        events.append((
            "research_observed",
            truncate(normalized, 180),
            {
                "source_type": source_type,
                "title": truncate(normalized, 120),
                "locator": "assistant_turn",
                "claim": truncate(normalized, 240),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "tool_call_id": "",
                "evidence_quote_or_hash": truncate(normalized, 200),
                "evidence_source": "assistant_claim",
                "trust_level": "claim",
            },
        ))

    failure_category = ""
    if any(k in low for k in ("buy-hold", "buy hold", "baseline", "跑不赢基线")):
        failure_category = "baseline_underperformance"
    elif any(k in low for k in ("drawdown", "dd", "回撤")):
        failure_category = "drawdown_unacceptable"
    elif any(k in low for k in ("rolling", "oos", "out-of-sample", "样本外")):
        failure_category = "oos_instability"
    elif any(k in low for k in ("cost", "fee", "成本")):
        failure_category = "cost_drag"
    elif any(k in low for k in ("beta", "market-neutral", "market neutral")):
        failure_category = "beta_exposure"
    elif any(k in low for k in ("failed", "fail", "失败", "打掉", "不通过")):
        failure_category = "hypothesis_failed"
    if failure_category:
        events.append(("hypothesis_failed", truncate(normalized, 180), {"category": failure_category, "reason": truncate(normalized, 240)}))

    return events
