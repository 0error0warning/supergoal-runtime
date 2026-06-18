"""Persistent session goals — the Ralph loop for Hermes.

A goal is a free-form user objective that stays active across turns. After
each turn completes, a small judge call asks an auxiliary model "is this
goal satisfied by the assistant's last response?". If not, Hermes feeds a
continuation prompt back into the same session and keeps working until the
goal is done, turn budget is exhausted, the user pauses/clears it, or the
user sends a new message (which takes priority and pauses the goal loop).

State is persisted in SessionDB's ``state_meta`` table keyed by
``goal:<session_id>`` so ``/resume`` picks it up.

Design notes / invariants:

- The continuation prompt is just a normal user message appended to the
  session via ``run_conversation``. No system-prompt mutation, no toolset
  swap — prompt caching stays intact.
- Judge failures are fail-OPEN: ``continue``. A broken judge must not wedge
  progress; the turn budget is the backstop.
- When a real user message arrives mid-loop it preempts the continuation
  prompt and also pauses the goal loop for that turn (we still re-judge
  after, so if the user's message happens to complete the goal the judge
  will say ``done``).
- This module has zero hard dependency on ``cli.HermesCLI`` or the gateway
  runner — both wire the same ``GoalManager`` in.

Nothing in this module touches the agent's system prompt or toolset.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from hermes_cli.goal_events import (
    GoalEvent,
    append_goal_event as _append_goal_event_record,
    events_meta_key as _events_meta_key,
    load_goal_events as _load_goal_event_records,
)
from hermes_cli.supergoal.store import (
    _DB_CACHE as _STORE_DB_CACHE,
    GoalRunRepository,
    SessionBindingStore,
    get_session_db as _store_get_session_db,
    legacy_goal_run_id as _legacy_goal_run_id,
    new_goal_run_id as _new_goal_run_id,
)
from hermes_cli.supergoal.gates import (
    evaluate_gates as _gate_evaluate_gates,
    first_blocking_failure as _gate_first_blocking_failure,
    first_failed_gate as _gate_first_failed_gate,
    gate_eligible_evidence_count as _gate_eligible_count,
    has_verified_execution_evidence as _gate_has_verified_execution_evidence,
    hypothesis_complete as _gate_hypothesis_complete,
    hypothesis_has_verified_artifact as _gate_hypothesis_has_verified_artifact,
    is_blocking_gate as _gate_is_blocking_gate,
    is_gate_open as _gate_is_open,
    open_followups as _gate_open_followups,
    passed_gate_ids as _gate_passed_ids,
    reconcile_done_evidence_gates as _gate_reconcile_done_evidence_gates,
    set_gate_open as _gate_set_open,
    sync_evidence_layers_from_findings as _gate_sync_evidence_layers_from_findings,
    tool_backed_research_findings as _gate_tool_backed_research_findings,
    verified_hypothesis_artifact_count as _gate_verified_hypothesis_artifact_count,
)
from hermes_cli.supergoal.projection import project_events_to_board as _project_events_to_board
from hermes_cli.supergoal_gates import build_default_supergoal_gates
from hermes_cli.supergoal_projection import (
    artifact_paths as _artifact_paths,
    classify_action_text as _classify_action_text,
    extract_observation_events as _extract_supergoal_observation_events,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Constants & defaults
# ──────────────────────────────────────────────────────────────────────

DEFAULT_MAX_TURNS = 20
DEFAULT_JUDGE_TIMEOUT = 30.0
# Judge output budget. The freeform judge returns a one-line JSON verdict, but
# reasoning models (deepseek-v4, qwq, etc.) burn tokens on hidden reasoning
# before emitting the visible JSON — and the first /goal turn's prompt is
# larger than later turns, which pushes total reply length past tight caps.
# 200 tokens (the original default) reliably truncated the JSON on reasoning
# models, leaving '{"done": true, "reason": "The agent successfully' and
# triggering the auto-pause. 4096 covers reasoning + verdict on every model
# we've live-tested; override via auxiliary.goal_judge.max_tokens for
# specifically constrained setups.
DEFAULT_JUDGE_MAX_TOKENS = 4096
# Supergoal critic is a board updater, not the completion judge. Keep its
# default output budget lower so CPA/custom endpoints do not spend a long time
# reasoning before emitting the small JSON board delta. Users can override via
# auxiliary.supergoal_critic.max_tokens.
DEFAULT_SUPERGOAL_CRITIC_MAX_TOKENS = 1536
# Cap how much of the last response + recent messages we send to the judge.
_JUDGE_RESPONSE_SNIPPET_CHARS = 4000
# After this many consecutive judge *parse* failures (empty output / non-JSON),
# the loop auto-pauses and points the user at the goal_judge config. API /
# transport errors do NOT count toward this — those are transient. This guards
# against small models (e.g. deepseek-v4-flash) that cannot follow the strict
# JSON reply contract; without it the loop runs until the turn budget is
# exhausted with every reply shaped like `judge returned empty response` or
# `judge reply was not JSON`.
DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES = 3
DEFAULT_MAX_CONSECUTIVE_JUDGE_API_FAILURES = 5  # auto-pause after this many consecutive API errors
DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES = 3  # supergoal board/critic failures must fail-closed, not burn budget
DEFAULT_MAX_SAME_GATE_STALLS = 3  # pause instead of looping forever on the same deterministic gate veto


CONTINUATION_PROMPT_TEMPLATE = (
    "[Continuing toward your standing goal]\n"
    "Goal: {goal}\n\n"
    "Continue working toward this goal. Take the next concrete step. "
    "If you believe the goal is complete, state so explicitly and stop. "
    "If you are blocked and need input from the user, say so clearly and stop."
)

# Used when the user has added one or more /subgoal criteria. Surfaced
# to the agent verbatim so it sees what to target on the next turn,
# and surfaced to the judge so the verdict considers them too.
CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "[Continuing toward your standing goal]\n"
    "Goal: {goal}\n\n"
    "Additional criteria the user added mid-loop:\n"
    "{subgoals_block}\n\n"
    "Continue working toward the goal AND all additional criteria. Take "
    "the next concrete step. If you believe the goal and every "
    "additional criterion are complete, state so explicitly and stop. "
    "If you are blocked and need input from the user, say so clearly "
    "and stop."
)

# High-budget continuation prompt for /supergoal. Keep this in goals.py rather
# than sprinkling logic through CLI/gateway so the product behavior is owned by
# the same orchestration module as /goal. The design intentionally stays simple:
# a planner/executor/judge mindset, explicit verification, compact checkpoints,
# and recovery when stuck — enough structure to avoid drift without adding a
# brittle coordinator.
SUPERGOAL_CONTINUATION_PROMPT_TEMPLATE = (
    "[Continuing toward your SUPERGOAL — long-running autonomous mode]\n"
    "Supergoal: {goal}\n\n"
    "This is NOT ordinary /goal. Before executing literal instructions, infer "
    "the user's root intent and optimize for that objective. Use a lightweight "
    "first-principles loop each turn:\n"
    "1. Re-anchor on inferred user intent, success definition, constraints, and evidence/state.\n"
    "2. Model the problem from first principles: what must be true for the goal to succeed?\n"
    "3. Check whether existing tools/projects/APIs/patterns should be reused before building.\n"
    "4. Choose the shortest reliable path by ROI, risk, maintainability, and verifiability.\n"
    "5. Execute real tool-backed work, not just planning.\n"
    "6. Verify the result with commands, tests, files, logs, or other evidence.\n"
    "7. Leave a compact checkpoint: what changed, what was verified, what remains.\n\n"
    "Avoid long-running-agent failure modes: do not drift, do not do tiny safe "
    "busywork forever, do not repeat completed work, do not hide blockers, do "
    "not blindly implement the user's literal wording, and do not reinvent "
    "existing mature solutions without a reuse-vs-build reason. When blocked, "
    "expand the solution space instead of treating the first failure as a hard blocker.\n\n"
    "If the supergoal is complete, state that explicitly and stop. If blocked, "
    "state the blocker and the exact user input/action needed. Otherwise keep "
    "working on the next concrete step."
)

SUPERGOAL_BOARD_BLOCK_TEMPLATE = (
    "\n\nCurrent Supergoal State Board (authoritative working memory; update your "
    "behavior from it, do not repeat completed attempts):\n{board}\n\n"
    "Before acting, use the board to decide whether this is an intent-inference "
    "turn, first-principles modeling turn, existing-solution scan, root-cause "
    "diagnosis turn, verification turn, or replan turn. Prefer the next action "
    "that most reduces uncertainty, risk, or wasted bespoke work."
)

SUPERGOAL_REPLAN_BLOCK = (
    "\n\nREPLAN REQUIRED THIS TURN:\n"
    "- Do not continue the previous path by inertia.\n"
    "- Restate the inferred root intent and first-principles problem model.\n"
    "- Briefly compare at least 2 plausible strategies or hypotheses, including reuse of existing solutions if applicable.\n"
    "- Pick the best path by ROI, risk, maintainability, and verifiability.\n"
    "- Then execute one concrete tool-backed step on the selected path.\n"
    "- If a hard gate is open, satisfy that gate first; do not choose infrastructure work unless it is a proven dependency.\n"
    "- End with: Changed / Verified / Evidence / Remaining gates / Next action class / Why not the tempting alternative."
)

SUPERGOAL_HARD_GATE_BLOCK_TEMPLATE = (
    "\n\nHARD GATE / INERTIA GUARD:\n"
    "{reason}\n"
    "You are not allowed to continue the blocked action class by inertia. Work on the first failed gate instead. "
    "If the gate cannot be satisfied with available tools, produce a concise blocked or no-edge report with evidence."
)

SUPERGOAL_FAILURE_TAXONOMY_BLOCK_TEMPLATE = (
    "\n\nFAILURE TAXONOMY PHASE — DO NOT RUN ANOTHER ORDINARY BENCHMARK BY INERTIA:\n"
    "Repeated hypotheses have failed. This turn must first explain the failure pattern before proposing more experiments.\n"
    "Failure taxonomy so far: {taxonomy}\n"
    "Admission criteria for any new hypothesis: {criteria}\n"
    "Required order this turn:\n"
    "1. Cluster failed paths by root failure cause (beta/proxy overlap, OOS instability, drawdown, costs, sample size, data latency, or missing independent information).\n"
    "2. State which search families are now low-ROI and should be stopped.\n"
    "3. Either produce/update a no-edge attribution report, or propose ONE new hypothesis family that satisfies every admission criterion.\n"
    "4. If proposing a new hypothesis, explain the independent information source before writing code or running a benchmark.\n"
)

SUPERGOAL_CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "[Continuing toward your SUPERGOAL — long-running autonomous mode]\n"
    "Supergoal: {goal}\n\n"
    "Additional criteria the user added mid-loop:\n"
    "{subgoals_block}\n\n"
    "Operate like a day-long autonomous agent while satisfying the supergoal "
    "AND all additional criteria. Each turn: infer the user's root intent, "
    "reason from first principles, check existing solutions before building, "
    "choose the shortest reliable path, execute real tool-backed work, verify "
    "it, and leave a compact checkpoint. Avoid literalism, drift, repeated "
    "busywork, reinventing mature solutions, and unnecessary human prompts. "
    "If complete or blocked, say so explicitly; otherwise continue with the "
    "next concrete step."
)


JUDGE_SYSTEM_PROMPT = (
    "You are a strict judge evaluating whether an autonomous agent has "
    "achieved a user's stated goal. You receive the goal text and the "
    "agent's most recent response. Your only job is to decide whether "
    "the goal is fully satisfied based on that response.\n\n"
    "A goal is DONE only when:\n"
    "- The response explicitly confirms the goal was completed, OR\n"
    "- The response clearly shows the final deliverable was produced, OR\n"
    "- The response explains the goal is unachievable / blocked / needs "
    "user input (treat this as DONE with reason describing the block).\n\n"
    "Otherwise the goal is NOT done — CONTINUE.\n\n"
    "Reply ONLY with a single JSON object on one line:\n"
    '{\"done\": <true|false>, \"reason\": \"<one-sentence rationale>\"}'
)


JUDGE_USER_PROMPT_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Is the goal satisfied?"
)

# Used when the user has added /subgoal criteria. The judge must
# evaluate ALL of them being met, not just the original goal.
JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Additional criteria the user added mid-loop (all must also be "
    "satisfied for the goal to be DONE):\n{subgoals_block}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Decision: For each numbered criterion above, find concrete "
    "evidence in the agent's response that the criterion is "
    "satisfied. Do not accept generic phrases like 'all requirements "
    "met' or 'implying it was done' — require specific evidence (a "
    "file contents excerpt, an output line, a command result). If "
    "ANY criterion lacks specific evidence in the response, the goal "
    "is NOT done — return CONTINUE.\n\n"
    "Is the goal AND every additional criterion satisfied?"
)

SUPERGOAL_CRITIC_SYSTEM_PROMPT = (
    "You are a strategic critic for a long-running autonomous agent. "
    "Do not decide final completion; another judge handles DONE. Your job is "
    "to update compact working memory and detect bad long-horizon behavior: "
    "drift, repetition, weak progress, premature conclusions, missing evidence, "
    "literal task-following that misses root intent, insufficient first-principles "
    "reasoning, insufficient research into existing solutions, reinventing wheels, "
    "or the need to replan. Reply ONLY with one JSON object."
)

_ALLOWED_ACTION_CLASSES = {"research", "hypothesis_generation", "experiment_execution", "validation", "infra_engineering", "reporting", "safety", "unknown"}


SUPERGOAL_CRITIC_USER_PROMPT_TEMPLATE = (
    "Supergoal:\n{goal}\n\n"
    "Existing state board:\n{board}\n\n"
    "Most recent agent response:\n{response}\n\n"
    "Return JSON with these keys:\n"
    "{{\n"
    "  \"inferred_user_intent\": \"root objective in one sentence\",\n"
    "  \"success_definition\": \"what would actually satisfy the user\",\n"
    "  \"first_principles_model\": [\"load-bearing truths or constraints\"],\n"
    "  \"existing_solution_scan\": [\"mature tools/APIs/projects/patterns checked or to check\"],\n"
    "  \"research_findings\": [{{\"source_type\": \"paper|github|web|docs|benchmark|local|other\", \"title\": \"short name\", \"locator\": \"url/path/id\", \"claim\": \"what this source changes\", \"tool_call_id\": \"only if from actual tool provenance\", \"retrieved_at\": \"timestamp if known\", \"evidence_quote_or_hash\": \"quote/hash if known\"}}],\n"
    "  \"hypothesis_portfolio\": [{{\"id\": \"H1\", \"claim\": \"testable hypothesis\", \"why_plausible\": \"...\", \"data_needed\": \"...\", \"baseline\": \"...\", \"experiment\": \"...\", \"kill_criteria\": \"...\", \"expected_edge\": \"...\", \"risk\": \"...\", \"status\": \"proposed|running|passed|failed|killed\", \"artifacts\": [\"path/url/log\"], \"verdict_reason\": \"...\"}}],\n"
    "  \"current_action_class\": \"research|hypothesis_generation|experiment_execution|validation|infra_engineering|reporting|safety|unknown\",\n"
    "  \"action_proposal\": {{\"action_class\": \"research|hypothesis_generation|experiment_execution|validation|infra_engineering|reporting|safety|unknown\", \"target_gate_id\": \"first failed blocking gate id\", \"expected_evidence\": [\"specific evidence this action should create\"], \"tools_needed\": [\"tool names\"], \"max_turn_budget\": 1, \"risk_level\": \"low|medium|high\", \"why_this_gate_first\": \"why this gate is next\", \"stop_if\": [\"condition that should stop this path\"], \"override_reason\": \"only if not targeting first failed blocking gate\"}},\n"
    "  \"no_edge_report\": \"short attribution if tested hypotheses show no edge\",\n"
    "  \"build_vs_reuse_decision\": \"reuse|build|hybrid|unknown: rationale\",\n"
    "  \"literalism_risk\": \"low|medium|high\",\n"
    "  \"research_sufficiency\": \"sufficient|thin|missing\",\n"
    "  \"progress\": \"real|weak|none|regressed\",\n"
    "  \"strategy_health\": \"good|stuck|drifting|repeating|premature|blocked\",\n"
    "  \"root_cause_confidence\": 0.0,\n"
    "  \"should_replan\": false,\n"
    "  \"next_best_action\": \"one concrete next action\",\n"
    "  \"missing_evidence\": [\"...\"],\n"
    "  \"new_milestones\": [\"...\"],\n"
    "  \"new_hypotheses\": [\"...\"],\n"
    "  \"new_evidence\": [\"...\"],\n"
    "  \"new_attempted_solutions\": [\"...\"],\n"
    "  \"new_blockers\": [\"...\"],\n"
    "  \"new_risks\": [\"...\"]\n"
    "}}\n\n"
    "Keep every list item short and evidence-based. If the agent followed the "
    "literal task without inferring the user's root intent, skipped first-principles "
    "modeling, or built before checking mature existing solutions, set "
    "literalism_risk=high and/or research_sufficiency=thin/missing, request replan, "
    "and make next_best_action the smallest tool-backed scan/diagnostic. If unsure, "
    "set weak/none progress and request the smallest diagnostic action."
)


# ──────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────


@dataclass
class PlanStep:
    """A compact, durable plan step for /supergoal runs.

    This is intentionally small: it gives the loop a productized task graph
    surface without turning /goal into a project-management system.
    """

    id: str
    title: str
    status: str = "pending"  # pending | in_progress | done | failed | blocked | skipped
    verification: str = ""
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["PlanStep"]:
        if not isinstance(data, dict):
            return None
        step_id = str(data.get("id") or "").strip()
        title = str(data.get("title") or "").strip()
        if not step_id or not title:
            return None
        status = str(data.get("status") or "pending").strip() or "pending"
        if status not in {"pending", "in_progress", "done", "failed", "blocked", "skipped"}:
            status = "pending"
        return cls(
            id=step_id,
            title=title,
            status=status,
            verification=str(data.get("verification") or "").strip(),
            summary=str(data.get("summary") or "").strip(),
        )


@dataclass(frozen=True)
class GoalStatusField:
    label: str
    value: str
    level: str = "info"


@dataclass(frozen=True)
class GoalStatusControl:
    id: str
    label: str
    command: str
    style: str = "secondary"
    enabled: bool = True


@dataclass(frozen=True)
class GoalStatusCard:
    """Platform-neutral status surface for /goal and /supergoal UIs."""

    title: str
    status: str
    level: str
    color: str
    summary: str
    fields: List[GoalStatusField] = field(default_factory=list)
    controls: List[GoalStatusControl] = field(default_factory=list)
    debug: List[str] = field(default_factory=list)
    updated_at: float = 0.0
    session_short_id: str = ""
    plain_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "status": self.status,
            "level": self.level,
            "color": self.color,
            "summary": self.summary,
            "fields": [asdict(field) for field in self.fields],
            "controls": [asdict(control) for control in self.controls],
            "debug": list(self.debug),
            "updated_at": self.updated_at,
            "session_short_id": self.session_short_id,
            "plain_text": self.plain_text,
        }


def _short_session_id(session_id: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]", "", session_id or "")
    return clean[-8:] if clean else ""


def _goal_status_level(status: str, state: "GoalState") -> Tuple[str, str]:
    if status == "done":
        return "success", "green"
    if status == "paused":
        return "paused", "grey"
    if getattr(state, "blockers", None) or getattr(state, "hard_gate_reason", ""):
        return "blocked", "red"
    if getattr(state, "same_gate_stall_count", 0) >= 2:
        return "stalled", "orange"
    if getattr(state, "should_replan", False):
        return "needs_replan", "orange"
    if status == "active":
        return "running", "blue"
    return "info", "grey"


def _goal_status_controls(state: "GoalState") -> List[GoalStatusControl]:
    command = "/supergoal" if getattr(state, "mode", "goal") == "supergoal" else "/goal"
    level, _ = _goal_status_level(getattr(state, "status", ""), state)
    if state.status == "active":
        return [
            GoalStatusControl("pause", "Pause", f"{command} pause", "primary"),
            GoalStatusControl("refresh", "Refresh", f"{command} status"),
            GoalStatusControl("details", "Details", f"{command} status"),
            GoalStatusControl("mute", "Quiet", f"{command} status"),
            GoalStatusControl("clear", "Clear", f"{command} clear", "danger"),
        ]
    if state.status == "paused":
        return [
            GoalStatusControl("resume", "Resume", f"{command} resume", "primary"),
            GoalStatusControl("refresh", "Refresh", f"{command} status"),
            GoalStatusControl("details", "Details", f"{command} status"),
            GoalStatusControl("clear", "Clear", f"{command} clear", "danger"),
        ]
    if level in {"blocked", "stalled", "needs_replan"}:
        return [
            GoalStatusControl("resume", "Continue once", f"{command} resume", "primary"),
            GoalStatusControl("replan", "Replan", f"{command} replan", "secondary"),
            GoalStatusControl("details", "Details", f"{command} status"),
            GoalStatusControl("clear", "Clear", f"{command} clear", "danger"),
        ]
    if state.status == "done":
        return [
            GoalStatusControl("details", "Final report", f"{command} status"),
            GoalStatusControl("clear", "Archive", f"{command} clear", "secondary"),
        ]
    return [GoalStatusControl("refresh", "Refresh", f"{command} status")]


def build_goal_status_card(
    state: Optional["GoalState"],
    *,
    session_id: str = "",
    compact_text: str = "",
    include_debug: bool = False,
) -> GoalStatusCard:
    if state is None:
        return GoalStatusCard(
            title="No active goal",
            status="none",
            level="info",
            color="grey",
            summary="Set one with /goal <text>.",
            updated_at=time.time(),
            session_short_id=_short_session_id(session_id),
            plain_text=compact_text or "No active goal. Set one with /goal <text>.",
        )

    label = "Supergoal" if getattr(state, "mode", "goal") == "supergoal" else "Goal"
    level, color = _goal_status_level(state.status, state)
    first_gate = _first_failed_gate(state) if getattr(state, "mode", "goal") == "supergoal" else None
    current_step = _current_or_next_step(state) if getattr(state, "mode", "goal") == "supergoal" else None
    fields: List[GoalStatusField] = [
        GoalStatusField("Progress", f"{state.turns_used}/{state.max_turns} turns", "info"),
    ]
    if state.last_verdict or state.last_reason:
        verdict = state.last_verdict or "last"
        reason = f": {_truncate(state.last_reason, 120)}" if state.last_reason else ""
        fields.append(GoalStatusField("Last check", f"{verdict}{reason}", "info"))
    if current_step is not None:
        fields.append(GoalStatusField("Step", f"{current_step.id}: {current_step.title}", current_step.status))
    if first_gate is not None:
        fields.append(GoalStatusField("Gate", f"{first_gate.id}: {_truncate(first_gate.description, 120)}", first_gate.status))
    if state.next_best_action:
        fields.append(GoalStatusField("Next", _truncate(state.next_best_action, 160), "info"))
    if state.blockers:
        fields.append(GoalStatusField("Blocker", _truncate(state.blockers[-1], 160), "blocked"))
    if state.subgoals:
        fields.append(GoalStatusField("Subgoals", str(len(state.subgoals)), "info"))

    debug: List[str] = []
    if include_debug:
        debug = [
            f"status={state.status}",
            f"mode={state.mode}",
            f"strategy={state.strategy_health}",
            f"progress={state.progress}",
            f"critic_failures={state.consecutive_critic_failures}",
        ]
        if state.last_failed_gate_id:
            debug.append(f"gate_stall={state.last_failed_gate_id}:{state.same_gate_stall_count}")

    summary = _truncate(" ".join((state.goal or "").split()), 220)
    plain_lines = [
        f"{label} {state.status} · {state.turns_used}/{state.max_turns} turns",
        summary,
    ]
    for field in fields[1:5]:
        plain_lines.append(f"{field.label}: {field.value}")
    return GoalStatusCard(
        title=f"{label} status",
        status=state.status,
        level=level,
        color=color,
        summary=summary,
        fields=fields,
        controls=_goal_status_controls(state),
        debug=debug,
        updated_at=time.time(),
        session_short_id=_short_session_id(session_id),
        plain_text=compact_text or "\n".join(line for line in plain_lines if line),
    )


@dataclass
class ResearchFinding:
    """Provenanced research/evidence item for /supergoal.

    Critic-extracted findings are allowed into the board as hints, but they are
    not allowed to satisfy research gates unless a tool wrapper supplied durable
    provenance (tool_call_id or retrieved_at + quote/hash).
    """

    source_type: str
    title: str
    locator: str = ""
    claim: str = ""
    retrieved_at: str = ""
    tool_call_id: str = ""
    query: str = ""
    evidence_quote_or_hash: str = ""
    evidence_source: str = "assistant_claim"
    trust_level: str = "claim"  # claim | observed | verified
    relevance_score: float = 0.0
    contradiction: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_tool_backed(self) -> bool:
        if self.tool_call_id == "assistant_turn" or self.evidence_source == "assistant_claim" or self.trust_level == "claim":
            return False
        return bool(self.tool_call_id and self.trust_level in {"observed", "verified"})

    @classmethod
    def from_dict(cls, data: Any, *, infer_legacy_tool_backed: bool = False) -> Optional["ResearchFinding"]:
        if not isinstance(data, dict):
            return None
        source_type = str(data.get("source_type") or data.get("type") or "").strip().lower()
        title = str(data.get("title") or "").strip()
        if not source_type or not title:
            return None
        allowed = {"paper", "github", "web", "docs", "repo", "benchmark", "local", "other"}
        if source_type not in allowed:
            source_type = "other"
        tool_call_id = _truncate(" ".join(str(data.get("tool_call_id") or "").split()), 120)
        evidence_quote_or_hash = _truncate(" ".join(str(data.get("evidence_quote_or_hash") or data.get("quote") or data.get("hash") or "").split()), 400)
        raw_source = str(data.get("evidence_source") or data.get("source") or "").strip()
        raw_trust = str(data.get("trust_level") or "").strip()
        # Back-compat: older supergoal research records predate evidence_source
        # and trust_level, but may still carry a real tool_call_id plus quote/hash.
        # Do not silently downgrade those to assistant_claim; otherwise upgrades
        # erase valid G2 provenance and can strand active missions.
        legacy_tool_backed = bool(infer_legacy_tool_backed and tool_call_id and tool_call_id != "assistant_turn" and evidence_quote_or_hash)
        evidence_source = raw_source or ("tool_call" if legacy_tool_backed else "assistant_claim")
        trust_level = raw_trust or ("observed" if legacy_tool_backed else "claim")
        return cls(
            source_type=source_type,
            title=_truncate(" ".join(title.split()), 160),
            locator=_truncate(" ".join(str(data.get("locator") or data.get("url_or_path") or "").split()), 240),
            claim=_truncate(" ".join(str(data.get("claim") or "").split()), 240),
            retrieved_at=_truncate(" ".join(str(data.get("retrieved_at") or "").split()), 80),
            tool_call_id=tool_call_id,
            query=_truncate(" ".join(str(data.get("query") or "").split()), 240),
            evidence_quote_or_hash=evidence_quote_or_hash,
            evidence_source=_truncate(" ".join(evidence_source.split()), 80),
            trust_level=_truncate(" ".join(trust_level.split()), 20),
            relevance_score=_coerce_float(data.get("relevance_score"), 0.0),
            contradiction=bool(data.get("contradiction", False)),
        )


@dataclass
class HypothesisRecord:
    """Portfolio entry for research/trading/science-style supergoals."""

    id: str
    claim: str
    why_plausible: str = ""
    data_needed: str = ""
    baseline: str = ""
    experiment: str = ""
    kill_criteria: str = ""
    expected_edge: str = ""
    risk: str = ""
    status: str = "proposed"  # proposed | running | passed | failed | killed
    verdict_reason: str = ""
    artifacts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["HypothesisRecord"]:
        if isinstance(data, str):
            text = " ".join(data.split())
            if not text:
                return None
            return cls(id="H?", claim=_truncate(text, 240))
        if not isinstance(data, dict):
            return None
        claim = str(data.get("claim") or data.get("hypothesis") or "").strip()
        if not claim:
            return None
        hid = str(data.get("id") or data.get("name") or "H?").strip() or "H?"
        status = str(data.get("status") or "proposed").strip().lower()
        if status not in {"proposed", "running", "passed", "failed", "killed"}:
            status = "proposed"
        return cls(
            id=_truncate(hid, 32),
            claim=_truncate(" ".join(claim.split()), 240),
            why_plausible=_truncate(" ".join(str(data.get("why_plausible") or "").split()), 240),
            data_needed=_truncate(" ".join(str(data.get("data_needed") or "").split()), 240),
            baseline=_truncate(" ".join(str(data.get("baseline") or "").split()), 240),
            experiment=_truncate(" ".join(str(data.get("experiment") or "").split()), 300),
            kill_criteria=_truncate(" ".join(str(data.get("kill_criteria") or "").split()), 240),
            expected_edge=_truncate(" ".join(str(data.get("expected_edge") or "").split()), 160),
            risk=_truncate(" ".join(str(data.get("risk") or "").split()), 160),
            status=status,
            verdict_reason=_truncate(" ".join(str(data.get("verdict_reason") or "").split()), 240),
            artifacts=_clean_string_list(data.get("artifacts") or [], limit=8, item_limit=180),
        )


@dataclass
class GoalGate:
    """Deterministic gate evaluated before a supergoal can converge.

    ``blocking``/``kind`` separate acceptance gates from quality followups.  Old
    persisted gates only had id/description/status/verifier/evidence; loading
    still works and missing metadata is normalized from the gate id.
    """

    id: str
    description: str
    status: str = "pending"  # pending | passed | failed | blocked | not_applicable | followup
    verifier: str = ""
    evidence: str = ""
    phase: str = "verification"  # intent | research | execution | verification | finalization | safety
    kind: str = "run_acceptance"  # run_acceptance | quality_followup | safety_hard | domain_required
    blocking: bool = True
    verifier_id: str = ""
    required_evidence: List[str] = field(default_factory=list)
    stale_after_turns: Optional[int] = None
    missing: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["GoalGate"]:
        if not isinstance(data, dict):
            return None
        gid = str(data.get("id") or "").strip()
        desc = str(data.get("description") or data.get("title") or "").strip()
        if not gid or not desc:
            return None
        status = str(data.get("status") or "pending").strip().lower()
        if status not in {"pending", "passed", "failed", "blocked", "not_applicable", "followup"}:
            status = "pending"
        phase = str(data.get("phase") or "").strip().lower()
        kind = str(data.get("kind") or "").strip().lower()
        blocking_raw = data.get("blocking")
        if not phase or not kind or blocking_raw is None:
            phase, kind, default_blocking = _default_gate_metadata(gid)
            blocking = default_blocking if blocking_raw is None else bool(blocking_raw)
        else:
            blocking = bool(blocking_raw)
        if phase not in {"intent", "research", "execution", "verification", "finalization", "safety"}:
            phase = "verification"
        if kind not in {"run_acceptance", "quality_followup", "safety_hard", "domain_required"}:
            kind = "run_acceptance"
        if kind in {"run_acceptance", "domain_required", "safety_hard"}:
            blocking = True
        elif kind == "quality_followup":
            blocking = False
        return cls(
            id=_truncate(gid, 40),
            description=_truncate(" ".join(desc.split()), 240),
            status=status,
            verifier=_truncate(" ".join(str(data.get("verifier") or "").split()), 240),
            evidence=_truncate(" ".join(str(data.get("evidence") or "").split()), 300),
            phase=phase,
            kind=kind,
            blocking=blocking,
            verifier_id=_truncate(" ".join(str(data.get("verifier_id") or data.get("verifier") or "").split()), 120),
            required_evidence=_clean_string_list(data.get("required_evidence") or [], limit=12, item_limit=80),
            stale_after_turns=data.get("stale_after_turns") if isinstance(data.get("stale_after_turns"), int) else None,
            missing=_clean_string_list(data.get("missing") or [], limit=12, item_limit=120),
            reason=_truncate(" ".join(str(data.get("reason") or "").split()), 300),
        )


@dataclass
class SupergoalActionProposal:
    """Serializable action proposal reviewed by the supergoal controller."""

    action_class: str = "unknown"
    target_gate_id: str = ""
    expected_evidence: List[str] = field(default_factory=list)
    tools_needed: List[str] = field(default_factory=list)
    max_turn_budget: int = 1
    risk_level: str = "medium"
    why_this_gate_first: str = ""
    stop_if: List[str] = field(default_factory=list)
    text: str = ""
    override_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "SupergoalActionProposal":
        if not isinstance(data, dict):
            return cls()
        action_class = str(data.get("action_class") or "unknown").strip().lower() or "unknown"
        if action_class not in _ALLOWED_ACTION_CLASSES:
            action_class = "unknown"
        risk = str(data.get("risk_level") or "medium").strip().lower() or "medium"
        if risk not in {"low", "medium", "high"}:
            risk = "medium"
        try:
            budget = int(data.get("max_turn_budget", 1) or 1)
        except Exception:
            budget = 1
        return cls(
            action_class=action_class,
            target_gate_id=_truncate(" ".join(str(data.get("target_gate_id") or "").split()), 40),
            expected_evidence=_clean_string_list(data.get("expected_evidence") or [], limit=8, item_limit=120),
            tools_needed=_clean_string_list(data.get("tools_needed") or [], limit=8, item_limit=80),
            max_turn_budget=max(1, min(10, budget)),
            risk_level=risk,
            why_this_gate_first=_truncate(" ".join(str(data.get("why_this_gate_first") or data.get("why") or "").split()), 240),
            stop_if=_clean_string_list(data.get("stop_if") or [], limit=8, item_limit=120),
            text=_truncate(" ".join(str(data.get("text") or data.get("next_best_action") or "").split()), 300),
            override_reason=_truncate(" ".join(str(data.get("override_reason") or "").split()), 240),
        )

    def is_empty(self) -> bool:
        return not (self.action_class != "unknown" or self.target_gate_id or self.text)


@dataclass
class GoalState:
    """Serializable goal state stored per session."""

    goal: str
    goal_run_id: str = ""           # stable logical mission id; session_id is only a physical context version
    mode: str = "goal"             # goal | supergoal
    status: str = "active"          # active | paused | done | cleared | migrated
    turns_used: int = 0
    max_turns: int = DEFAULT_MAX_TURNS
    created_at: float = 0.0
    last_turn_at: float = 0.0
    last_verdict: Optional[str] = None        # "done" | "continue" | "skipped"
    last_reason: Optional[str] = None
    paused_reason: Optional[str] = None       # why we auto-paused (budget, etc.)
    consecutive_parse_failures: int = 0       # judge-output parse failures in a row
    consecutive_judge_api_failures: int = 0   # judge API/server errors in a row
    consecutive_critic_failures: int = 0      # supergoal critic parse/API/no-op failures in a row
    last_failed_gate_id: str = ""
    same_gate_stall_count: int = 0
    last_continuation_enqueued_at: float = 0.0
    last_continuation_kind: Optional[str] = None
    # /supergoal structured working memory. Normal /goal ignores these fields.
    acceptance_criteria: List[str] = field(default_factory=list)
    milestones: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    attempted_solutions: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    inferred_user_intent: str = ""
    success_definition: str = ""
    first_principles_model: List[str] = field(default_factory=list)
    existing_solution_scan: List[str] = field(default_factory=list)
    research_findings: List[ResearchFinding] = field(default_factory=list)
    hypothesis_portfolio: List[HypothesisRecord] = field(default_factory=list)
    gates: List[GoalGate] = field(default_factory=list)
    action_history: List[str] = field(default_factory=list)
    current_action_class: str = "unknown"
    action_proposal: SupergoalActionProposal = field(default_factory=SupergoalActionProposal)
    last_action_evidence_count: int = 0
    same_action_no_evidence_count: int = 0
    hard_gate_reason: str = ""
    no_edge_report: str = ""
    build_vs_reuse_decision: str = ""
    evidence_layers: Dict[str, List[str]] = field(default_factory=dict)
    failure_taxonomy: Dict[str, int] = field(default_factory=dict)
    search_phase: str = "explore"
    admission_criteria: List[str] = field(default_factory=list)
    literalism_risk: str = "unknown"
    research_sufficiency: str = "unknown"
    next_best_action: str = ""
    strategy_health: str = "unknown"
    progress: str = "unknown"
    root_cause_confidence: float = 0.0
    should_replan: bool = False
    replan_count: int = 0
    plan_steps: List[PlanStep] = field(default_factory=list)
    current_step_id: str = ""
    permission_mode: str = "supervised"  # supervised | full_auto
    permission_contract: Dict[str, Any] = field(default_factory=dict)
    event_count: int = 0
    last_event_type: Optional[str] = None
    status_card_message_ids: Dict[str, str] = field(default_factory=dict)
    # User-added criteria appended mid-loop via the /subgoal command.
    # When non-empty the judge prompt and continuation prompt both
    # include them so the agent works toward them and the judge factors
    # them into the verdict. Backwards-compatible: defaults to empty so
    # old state_meta rows load unchanged.
    subgoals: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        data = asdict(self)
        data["plan_steps"] = [step.to_dict() for step in self.plan_steps]
        data["research_findings"] = [finding.to_dict() for finding in self.research_findings]
        data["hypothesis_portfolio"] = [h.to_dict() for h in self.hypothesis_portfolio]
        data["gates"] = [g.to_dict() for g in self.gates]
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "GoalState":
        data = json.loads(raw)
        raw_subgoals = data.get("subgoals") or []
        subgoals: List[str] = []
        if isinstance(raw_subgoals, list):
            subgoals = [str(s).strip() for s in raw_subgoals if str(s).strip()]
        raw_steps = data.get("plan_steps") or []
        plan_steps: List[PlanStep] = []
        if isinstance(raw_steps, list):
            for raw_step in raw_steps:
                step = PlanStep.from_dict(raw_step)
                if step is not None:
                    plan_steps.append(step)
        raw_findings = data.get("research_findings") or []
        research_findings: List[ResearchFinding] = []
        if isinstance(raw_findings, list):
            for raw_finding in raw_findings:
                finding = ResearchFinding.from_dict(raw_finding, infer_legacy_tool_backed=True)
                if finding is not None:
                    research_findings.append(finding)
        raw_portfolio = data.get("hypothesis_portfolio") or data.get("hypotheses_detail") or []
        hypothesis_portfolio: List[HypothesisRecord] = []
        if isinstance(raw_portfolio, list):
            for raw_hypothesis in raw_portfolio:
                hypothesis = HypothesisRecord.from_dict(raw_hypothesis)
                if hypothesis is not None:
                    hypothesis_portfolio.append(hypothesis)
        raw_gates = data.get("gates") or []
        gates: List[GoalGate] = []
        if isinstance(raw_gates, list):
            for raw_gate in raw_gates:
                gate = GoalGate.from_dict(raw_gate)
                if gate is not None:
                    gates.append(gate)
        return cls(
            goal=data.get("goal", ""),
            goal_run_id=str(data.get("goal_run_id") or "").strip(),
            mode=data.get("mode", "goal") or "goal",
            status=data.get("status", "active"),
            turns_used=int(data.get("turns_used", 0) or 0),
            max_turns=int(data.get("max_turns", DEFAULT_MAX_TURNS) or DEFAULT_MAX_TURNS),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            last_turn_at=float(data.get("last_turn_at", 0.0) or 0.0),
            last_verdict=data.get("last_verdict"),
            last_reason=data.get("last_reason"),
            paused_reason=data.get("paused_reason"),
            consecutive_parse_failures=int(data.get("consecutive_parse_failures", 0) or 0),
            consecutive_judge_api_failures=int(data.get("consecutive_judge_api_failures", 0) or 0),
            consecutive_critic_failures=int(data.get("consecutive_critic_failures", 0) or 0),
            last_failed_gate_id=str(data.get("last_failed_gate_id") or "").strip(),
            same_gate_stall_count=int(data.get("same_gate_stall_count", 0) or 0),
            last_continuation_enqueued_at=float(data.get("last_continuation_enqueued_at", 0.0) or 0.0),
            last_continuation_kind=data.get("last_continuation_kind"),
            acceptance_criteria=_clean_string_list(data.get("acceptance_criteria") or []),
            milestones=_clean_string_list(data.get("milestones") or []),
            hypotheses=_clean_string_list(data.get("hypotheses") or []),
            evidence=_clean_string_list(data.get("evidence") or []),
            attempted_solutions=_clean_string_list(data.get("attempted_solutions") or []),
            blockers=_clean_string_list(data.get("blockers") or []),
            risks=_clean_string_list(data.get("risks") or []),
            inferred_user_intent=str(data.get("inferred_user_intent") or "").strip(),
            success_definition=str(data.get("success_definition") or "").strip(),
            first_principles_model=_clean_string_list(data.get("first_principles_model") or []),
            existing_solution_scan=_clean_string_list(data.get("existing_solution_scan") or []),
            research_findings=research_findings,
            hypothesis_portfolio=hypothesis_portfolio,
            gates=gates,
            action_history=_clean_string_list(data.get("action_history") or [], limit=12, item_limit=80),
            current_action_class=str(data.get("current_action_class") or "unknown").strip() or "unknown",
            action_proposal=SupergoalActionProposal.from_dict(data.get("action_proposal") or {}),
            last_action_evidence_count=int(data.get("last_action_evidence_count", 0) or 0),
            same_action_no_evidence_count=int(data.get("same_action_no_evidence_count", 0) or 0),
            hard_gate_reason=str(data.get("hard_gate_reason") or "").strip(),
            no_edge_report=str(data.get("no_edge_report") or "").strip(),
            build_vs_reuse_decision=str(data.get("build_vs_reuse_decision") or "").strip(),
            evidence_layers={
                str(k): _clean_string_list(v, limit=12, item_limit=160)
                for k, v in (data.get("evidence_layers") or {}).items()
                if str(k).strip()
            } if isinstance(data.get("evidence_layers"), dict) else {},
            failure_taxonomy={
                str(k): int(v or 0)
                for k, v in (data.get("failure_taxonomy") or {}).items()
                if str(k).strip()
            } if isinstance(data.get("failure_taxonomy"), dict) else {},
            search_phase=str(data.get("search_phase") or "explore").strip() or "explore",
            admission_criteria=_clean_string_list(data.get("admission_criteria") or [], limit=8, item_limit=180),
            literalism_risk=str(data.get("literalism_risk") or "unknown").strip() or "unknown",
            research_sufficiency=str(data.get("research_sufficiency") or "unknown").strip() or "unknown",
            next_best_action=str(data.get("next_best_action") or "").strip(),
            strategy_health=str(data.get("strategy_health") or "unknown").strip() or "unknown",
            progress=str(data.get("progress") or "unknown").strip() or "unknown",
            root_cause_confidence=_coerce_float(data.get("root_cause_confidence"), 0.0),
            should_replan=bool(data.get("should_replan", False)),
            replan_count=int(data.get("replan_count", 0) or 0),
            plan_steps=plan_steps,
            current_step_id=str(data.get("current_step_id") or "").strip(),
            permission_mode=str(data.get("permission_mode") or "supervised").strip().lower() if str(data.get("permission_mode") or "supervised").strip().lower() in {"supervised", "full_auto"} else "supervised",
            permission_contract=data.get("permission_contract") if isinstance(data.get("permission_contract"), dict) else {},
            event_count=int(data.get("event_count", 0) or 0),
            last_event_type=data.get("last_event_type"),
            status_card_message_ids={
                str(k): str(v)
                for k, v in (data.get("status_card_message_ids") or {}).items()
                if str(k).strip() and str(v).strip()
            } if isinstance(data.get("status_card_message_ids"), dict) else {},
            subgoals=subgoals,
        )

    # --- subgoals helpers -------------------------------------------------

    def render_subgoals_block(self) -> str:
        """Render the subgoals as a numbered ``- N. text`` block. Empty
        when no subgoals exist."""
        if not self.subgoals:
            return ""
        return "\n".join(f"- {i}. {text}" for i, text in enumerate(self.subgoals, start=1))

    def render_supergoal_board(self) -> str:
        """Compact persistent board injected into /supergoal continuations."""
        parts = [
            f"objective: {self.goal}",
            f"progress: {self.progress}; strategy_health: {self.strategy_health}; root_cause_confidence: {self.root_cause_confidence:.2f}",
            f"literalism_risk: {self.literalism_risk}; research_sufficiency: {self.research_sufficiency}",
        ]
        if self.inferred_user_intent:
            parts.append("inferred_user_intent: " + self.inferred_user_intent)
        if self.success_definition:
            parts.append("success_definition: " + self.success_definition)
        if self.first_principles_model:
            parts.append("first_principles_model: " + "; ".join(self.first_principles_model[-8:]))
        if self.existing_solution_scan:
            parts.append("existing_solution_scan: " + "; ".join(self.existing_solution_scan[-8:]))
        if self.research_findings:
            rendered_findings = []
            for finding in self.research_findings[-8:]:
                locator = f" <{finding.locator}>" if finding.locator else ""
                backed = "tool" if finding.is_tool_backed else "critic"
                rendered_findings.append(f"{finding.source_type}/{backed}:{finding.title}{locator}")
            parts.append("research_findings: " + "; ".join(rendered_findings))
        if self.hypothesis_portfolio:
            rendered_h = []
            for h in self.hypothesis_portfolio[-8:]:
                verdict = f" -> {h.verdict_reason}" if h.verdict_reason else ""
                rendered_h.append(f"{h.id}:{h.status}:{h.claim}{verdict}")
            parts.append("hypothesis_portfolio: " + "; ".join(rendered_h))
        if self.gates:
            first_failed = _first_failed_gate(self)
            rendered_g = [f"{g.id}{'*' if first_failed and g.id == first_failed.id else ''}:{g.status}:{g.description}" for g in self.gates[:8]]
            parts.append("gates: " + "; ".join(rendered_g))
        if self.action_history:
            parts.append("action_history: " + " -> ".join(self.action_history[-8:]))
        if self.current_action_class and self.current_action_class != "unknown":
            parts.append("current_action_class: " + self.current_action_class)
        if getattr(self, "action_proposal", None) and not self.action_proposal.is_empty():
            proposal = self.action_proposal
            parts.append(
                "action_proposal: "
                + json.dumps(
                    {
                        "action_class": proposal.action_class,
                        "target_gate_id": proposal.target_gate_id,
                        "expected_evidence": proposal.expected_evidence,
                        "tools_needed": proposal.tools_needed,
                        "max_turn_budget": proposal.max_turn_budget,
                        "risk_level": proposal.risk_level,
                        "why_this_gate_first": proposal.why_this_gate_first,
                        "stop_if": proposal.stop_if,
                        "override_reason": proposal.override_reason,
                        "text": proposal.text,
                    },
                    ensure_ascii=False,
                )
            )
        if self.hard_gate_reason:
            parts.append("hard_gate: " + self.hard_gate_reason)
        if self.no_edge_report:
            parts.append("no_edge_report: " + self.no_edge_report)
        if self.build_vs_reuse_decision:
            parts.append("build_vs_reuse_decision: " + self.build_vs_reuse_decision)
        if self.search_phase and self.search_phase != "explore":
            parts.append("search_phase: " + self.search_phase)
        if self.failure_taxonomy:
            ranked = sorted(self.failure_taxonomy.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
            parts.append("failure_taxonomy: " + "; ".join(f"{k}={v}" for k, v in ranked))
        if self.evidence_layers:
            parts.append("evidence_layers: " + "; ".join(f"{k}={len(v)}" for k, v in sorted(self.evidence_layers.items())))
        if self.admission_criteria:
            parts.append("admission_criteria: " + "; ".join(self.admission_criteria[-6:]))
        if self.acceptance_criteria:
            parts.append("acceptance_criteria: " + "; ".join(self.acceptance_criteria[:6]))
        if self.milestones:
            parts.append("milestones: " + "; ".join(self.milestones[-8:]))
        if self.hypotheses:
            parts.append("hypotheses: " + "; ".join(self.hypotheses[-8:]))
        if self.evidence:
            parts.append("evidence: " + "; ".join(self.evidence[-10:]))
        if self.attempted_solutions:
            parts.append("attempted_solutions: " + "; ".join(self.attempted_solutions[-8:]))
        if self.blockers:
            parts.append("blockers: " + "; ".join(self.blockers[-6:]))
        if self.risks:
            parts.append("risks: " + "; ".join(self.risks[-6:]))
        if self.plan_steps:
            rendered_steps = []
            for step in self.plan_steps[:8]:
                marker = "*" if step.id == self.current_step_id else ""
                rendered_steps.append(f"{step.id}{marker}:{step.status}:{step.title}")
            parts.append("plan_steps: " + "; ".join(rendered_steps))
        if self.next_best_action:
            parts.append("next_best_action: " + self.next_best_action)
        if self.should_replan:
            parts.append("replan: required")
        if self.replan_count:
            parts.append(f"replan_count: {self.replan_count}")
        if self.event_count:
            parts.append(f"events: {self.event_count}; last_event: {self.last_event_type or 'unknown'}")
        return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────
# Persistence (SessionDB state_meta)
# ──────────────────────────────────────────────────────────────────────


_BINDINGS = SessionBindingStore()
_RUNS = GoalRunRepository()
# Backwards-compatible test hook: existing tests clear goals._DB_CACHE.
_DB_CACHE = _STORE_DB_CACHE


def _get_session_db() -> Optional[Any]:
    """Compatibility wrapper for tests/callers that reset goals._DB_CACHE."""
    return _store_get_session_db()


def _get_bound_goal_run_id(session_id: str) -> str:
    return _BINDINGS.get_goal_run_id(session_id)


def _bind_session_to_goal_run(session_id: str, goal_run_id: str, *, reason: str = "") -> None:
    _BINDINGS.bind(session_id, goal_run_id, reason=reason)


def _load_goal_run(goal_run_id: str) -> Optional[GoalState]:
    raw = _RUNS.get_raw(goal_run_id)
    if not raw:
        return None
    try:
        state = GoalState.from_json(raw)
    except Exception as exc:
        logger.warning("GoalManager: could not parse stored goal run %s: %s", goal_run_id, exc)
        return None
    if not state.goal_run_id:
        state.goal_run_id = goal_run_id
    return state


def _load_legacy_session_goal(session_id: str) -> Optional[GoalState]:
    raw = _RUNS.get_legacy_raw(session_id)
    if not raw:
        return None
    try:
        state = GoalState.from_json(raw)
    except Exception as exc:
        logger.warning("GoalManager: could not parse stored goal for %s: %s", session_id, exc)
        return None
    if not state.goal_run_id:
        state.goal_run_id = _legacy_goal_run_id(session_id)
    return state


def _event_log_id_for_session(session_id: str) -> str:
    bound = _get_bound_goal_run_id(session_id)
    if bound:
        return bound
    state = _load_legacy_session_goal(session_id)
    if state and state.goal_run_id:
        return state.goal_run_id
    return session_id


def _merge_legacy_events_into_goal_run(session_id: str, goal_run_id: str, *, max_events: int = 200) -> None:
    """Move pre-goal_run_id events from goal_events:<session_id> to goal_events:<goal_run_id>."""
    if not session_id or not goal_run_id or session_id == goal_run_id:
        return
    legacy_events = _load_goal_event_records(session_id, limit=max_events)
    if not legacy_events:
        return
    current_events = _load_goal_event_records(goal_run_id, limit=max_events)
    merged: List[GoalEvent] = []
    seen = set()
    for event in [*current_events, *legacy_events]:
        key = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)
    merged = sorted(merged, key=lambda e: e.ts)[-max_events:]
    db = _get_session_db()
    if db is None:
        return
    try:
        db.set_meta(_events_meta_key(goal_run_id), json.dumps([e.to_dict() for e in merged], ensure_ascii=False))
    except Exception as exc:
        logger.debug("GoalManager: legacy event migration failed: %s", exc)


def load_goal(session_id: str) -> Optional[GoalState]:
    """Load the logical goal run bound to a physical session id."""
    if not session_id:
        return None
    goal_run_id = _get_bound_goal_run_id(session_id)
    state = _load_goal_run(goal_run_id) if goal_run_id else None
    if state is None:
        state = _load_legacy_session_goal(session_id)
        if state is not None:
            # Lazy migration for pre-goal_run_id rows: bind the physical
            # session to a stable logical run and materialize goal_run:<id>.
            _merge_legacy_events_into_goal_run(session_id, state.goal_run_id)
            save_goal(session_id, state)
    if state is not None and state.goal_run_id:
        events = _load_goal_event_records(state.goal_run_id, limit=200)
        if events:
            state.event_count = len(events)
            state.last_event_type = events[-1].type
    return state


def save_goal(session_id: str, state: GoalState) -> None:
    """Persist a logical goal run and bind this physical session to it."""
    if not session_id or state is None:
        return
    if not state.goal_run_id:
        state.goal_run_id = _new_goal_run_id()
    _bind_session_to_goal_run(session_id, state.goal_run_id)
    raw = state.to_json()
    _RUNS.set_raw(state.goal_run_id, raw)
    # Compatibility mirror for old callers/tools that inspect goal:<session_id>.
    _RUNS.set_legacy_raw(session_id, raw)


def clear_goal(session_id: str) -> None:
    """Mark a goal cleared in the DB (preserved for audit, status=cleared)."""
    state = load_goal(session_id)
    if state is None:
        return
    state.status = "cleared"
    save_goal(session_id, state)


def migrate_goal_state(old_session_id: str, new_session_id: str, *, reason: str = "compression") -> Optional[GoalState]:
    """Bind a new physical session id to the existing logical goal_run_id.

    Compression rotates session_id/context, but it does not create a new
    mission.  The durable mission state remains in ``goal_run:<goal_run_id>``;
    both old and new session ids point at that same logical run.
    """
    if not old_session_id or not new_session_id or old_session_id == new_session_id:
        return load_goal(new_session_id) if new_session_id else None
    state = load_goal(old_session_id)
    if state is None:
        return load_goal(new_session_id)
    if not state.goal_run_id:
        state.goal_run_id = _new_goal_run_id()
        save_goal(old_session_id, state)
    existing_new = load_goal(new_session_id)
    if (
        existing_new is not None
        and existing_new.status in {"active", "paused", "done"}
        and existing_new.goal_run_id
        and existing_new.goal_run_id != state.goal_run_id
    ):
        return existing_new
    _bind_session_to_goal_run(new_session_id, state.goal_run_id, reason=reason)
    # Keep a compatibility mirror for tools still looking at goal:<new_session_id>.
    save_goal(new_session_id, state)
    append_goal_event(
        new_session_id,
        "session_rotated",
        turn=state.turns_used,
        summary=f"session rotated {old_session_id} -> {new_session_id} ({reason})",
        data={"old_session_id": old_session_id, "new_session_id": new_session_id, "reason": reason},
    )
    return load_goal(new_session_id)


def load_goal_events(session_id: str, *, limit: int = 100) -> List[GoalEvent]:
    """Load events for the logical goal run bound to ``session_id``."""
    return _load_goal_event_records(_event_log_id_for_session(session_id), limit=limit)


def append_goal_event(
    session_id: str,
    event_type: str,
    *,
    turn: int = 0,
    summary: str = "",
    data: Optional[Dict[str, Any]] = None,
    max_events: int = 200,
) -> Optional[GoalEvent]:
    """Append an event to the logical goal run and mirror counters into state."""
    event_log_id = _event_log_id_for_session(session_id)
    event = _append_goal_event_record(
        event_log_id,
        event_type,
        turn=turn,
        summary=summary,
        data=data,
        max_events=max_events,
    )
    if event is None:
        return None
    events = _load_goal_event_records(event_log_id, limit=max_events)
    state = load_goal(session_id)
    if state is not None:
        state.event_count = len(events)
        state.last_event_type = event.type
        save_goal(session_id, state)
    return event


# ──────────────────────────────────────────────────────────────────────
# Judge
# ──────────────────────────────────────────────────────────────────────


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "… [truncated]"


def _clean_string_list(value: Any, *, limit: int = 12, item_limit: int = 220) -> List[str]:
    """Normalize model/user supplied list-ish values into compact strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        text = " ".join(text.split())
        if len(text) > item_limit:
            text = text[:item_limit].rstrip() + "…"
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed


def _merge_compact_list(existing: List[str], new_items: Any, *, max_items: int = 20) -> List[str]:
    merged = list(existing or [])
    seen = {str(x).strip().lower() for x in merged if str(x).strip()}
    for item in _clean_string_list(new_items, limit=20):
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-max_items:]


def _merge_research_findings(existing: List[ResearchFinding], new_items: Any, *, max_items: int = 24) -> List[ResearchFinding]:
    merged = list(existing or [])
    seen = {(f.source_type, f.title.lower(), f.locator.lower()) for f in merged}
    if isinstance(new_items, dict):
        new_items = [new_items]
    if not isinstance(new_items, list):
        return merged[-max_items:]
    for item in new_items:
        finding = ResearchFinding.from_dict(item)
        if finding is None:
            continue
        key = (finding.source_type, finding.title.lower(), finding.locator.lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(finding)
    return merged[-max_items:]


def _tool_backed_research_findings(state: GoalState) -> List[ResearchFinding]:
    return _gate_tool_backed_research_findings(state)


def _research_finding_types(state: GoalState) -> set:
    # Gate only on tool-backed provenance. Critic-extracted source hints stay visible
    # on the board but cannot make research "sufficient" by themselves.
    return {f.source_type for f in _tool_backed_research_findings(state)}


def _research_sufficiency_from_findings(state: GoalState, critic_value: str = "") -> str:
    """Gate research sufficiency on tool-backed source diversity, not critic prose."""
    types = _research_finding_types(state)
    external_types = types.intersection({"paper", "github", "web", "docs", "benchmark"})
    if {"paper", "github"}.issubset(types) or len(external_types) >= 3:
        return "sufficient"
    if external_types:
        return "thin"
    if any(f.source_type in {"benchmark", "local"} for f in _tool_backed_research_findings(state)):
        # Local tool-backed experiments/benchmarks are real progress for
        # maintenance/debugging/trading supergoals, but they are not enough to
        # satisfy the external-provenance research gate by themselves.
        return "thin"
    if critic_value in {"thin", "missing"}:
        return critic_value
    return "missing"


def _fallback_action_proposal(state: GoalState, text: str = "") -> SupergoalActionProposal:
    first_gate = _first_blocking_failed_gate(state)
    action_text = text or state.next_best_action or (f"Satisfy gate {first_gate.id}: {first_gate.description}" if first_gate else "")
    return SupergoalActionProposal(
        action_class=_classify_action_text(action_text),
        target_gate_id=first_gate.id if first_gate else "",
        expected_evidence=[first_gate.description] if first_gate else [],
        tools_needed=[],
        max_turn_budget=1,
        risk_level="medium",
        why_this_gate_first="first failed blocking gate" if first_gate else "fallback action proposal",
        stop_if=["evidence does not increase after this turn"],
        text=_truncate(" ".join(action_text.split()), 300),
    )


def _proposal_from_critic_data(state: GoalState, data: Dict[str, Any]) -> SupergoalActionProposal:
    raw = data.get("action_proposal")
    if isinstance(raw, dict):
        proposal = SupergoalActionProposal.from_dict(raw)
    else:
        proposal = SupergoalActionProposal()
    nba = str(data.get("next_best_action") or "").strip()
    current_action = str(data.get("current_action_class") or "").strip().lower()
    if proposal.is_empty():
        proposal = _fallback_action_proposal(state, nba)
    if current_action in _ALLOWED_ACTION_CLASSES and current_action != "unknown" and proposal.action_class == "unknown":
        proposal.action_class = current_action
    if nba and not proposal.text:
        proposal.text = _truncate(" ".join(nba.split()), 300)
    first_gate = _first_blocking_failed_gate(state)
    if first_gate is not None and not proposal.target_gate_id:
        proposal.target_gate_id = first_gate.id
        proposal.why_this_gate_first = proposal.why_this_gate_first or "first failed blocking gate"
        if not proposal.expected_evidence:
            proposal.expected_evidence = [first_gate.description]
    return proposal


def _evidence_ref_count(state: GoalState) -> int:
    """Board-visible evidence count, including claim-level hints.

    Do not use this for stall/inertia progress. It intentionally includes
    assistant claims so status cards and prompts can show what the model said it
    did, but claims are not gate-eligible evidence.
    """
    return sum(len(v or []) for v in (state.evidence_layers or {}).values()) + len(state.evidence or []) + len(state.research_findings or [])


def _hypothesis_has_verified_artifact(h: HypothesisRecord) -> bool:
    return _gate_hypothesis_has_verified_artifact(h)


def _verified_hypothesis_artifact_count(state: GoalState) -> int:
    return _gate_verified_hypothesis_artifact_count(state)


def _gate_eligible_evidence_count(state: GoalState) -> int:
    return _gate_eligible_count(state)


def _has_verified_execution_evidence(state: GoalState) -> bool:
    return _gate_has_verified_execution_evidence(state)


def _merge_hypothesis_portfolio(existing: List[HypothesisRecord], new_items: Any, *, max_items: int = 16) -> List[HypothesisRecord]:
    merged = list(existing or [])
    seen = {(h.id.lower(), h.claim.lower()) for h in merged}
    if isinstance(new_items, (dict, str)):
        new_items = [new_items]
    if not isinstance(new_items, list):
        return merged[-max_items:]
    next_num = len(merged) + 1
    for item in new_items:
        hypothesis = HypothesisRecord.from_dict(item)
        if hypothesis is None:
            continue
        if hypothesis.id == "H?":
            hypothesis.id = f"H{next_num}"
            next_num += 1
        key = (hypothesis.id.lower(), hypothesis.claim.lower())
        claim_seen = {h.claim.lower() for h in merged}
        if key in seen or hypothesis.claim.lower() in claim_seen:
            continue
        seen.add(key)
        merged.append(hypothesis)
    return merged[-max_items:]


def _default_gate_metadata(gate_id: str) -> tuple[str, str, bool]:
    """Best-effort metadata for legacy persisted gates without typed fields."""
    gid = (gate_id or "").upper()
    if gid == "G1":
        return "intent", "run_acceptance", True
    if gid == "G2":
        # Legacy sessions treated G2 as blocking.  Fresh generic goals get a
        # non-blocking G2 from GateSpec; this conservative default preserves old
        # domain/research runs until ensure_gates can merge newer specs.
        return "research", "domain_required", True
    if gid == "G3":
        return "execution", "run_acceptance", True
    if gid == "G4":
        return "finalization", "run_acceptance", True
    if gid.startswith("SG-"):
        return "verification", "domain_required", True
    if gid.startswith("SAFE") or gid.startswith("SAFETY"):
        return "safety", "safety_hard", True
    if gid.startswith("MON"):
        return "verification", "quality_followup", False
    return "verification", "run_acceptance", True


def _default_supergoal_gates(goal: str = "") -> List[GoalGate]:
    return build_default_supergoal_gates(goal, GoalGate)


def _merge_gate_metadata(existing: GoalGate, default: GoalGate) -> None:
    """Upgrade persisted gates with newer typed metadata without clobbering status/evidence."""
    existing.description = default.description or existing.description
    existing.phase = default.phase
    existing.kind = default.kind
    existing.blocking = default.blocking
    existing.verifier_id = default.verifier_id
    existing.required_evidence = list(default.required_evidence or [])
    existing.stale_after_turns = default.stale_after_turns
    if default.verifier:
        existing.verifier = default.verifier


def _ensure_supergoal_gates_for_text(state: GoalState, text: str = "") -> None:
    """Ensure the gate set matches the latest goal/subgoal intent.

    Subgoals can introduce a strategy/hypothesis requirement after the original
    /supergoal was created. Gate creation must therefore be incremental rather
    than a one-shot at set().
    """
    if getattr(state, "mode", "goal") != "supergoal":
        return
    combined = " ".join([state.goal, text or "", " ".join(state.subgoals or [])])
    defaults = _default_supergoal_gates(combined)
    by_id = {g.id: g for g in state.gates or []}
    for gate in defaults:
        existing = by_id.get(gate.id)
        if existing is None:
            state.gates.append(gate)
            by_id[gate.id] = gate
        else:
            _merge_gate_metadata(existing, gate)


def _is_gate_open(gate: GoalGate) -> bool:
    return _gate_is_open(gate)


def _is_blocking_gate(gate: GoalGate) -> bool:
    return _gate_is_blocking_gate(gate)


def _first_failed_gate(state: GoalState) -> Optional[GoalGate]:
    return _gate_first_failed_gate(state)  # type: ignore[return-value]


def _first_blocking_failed_gate(state: GoalState) -> Optional[GoalGate]:
    return _gate_first_blocking_failure(state)  # type: ignore[return-value]


def _open_followup_gates(state: GoalState) -> List[GoalGate]:
    return list(_gate_open_followups(state))  # type: ignore[list-item]


def _passed_gate_ids(state: GoalState) -> set[str]:
    return _gate_passed_ids(state)


def _reconcile_done_evidence_gates(state: GoalState, last_response: str, judge_reason: str) -> List[str]:
    return _gate_reconcile_done_evidence_gates(state, last_response, judge_reason)


def _reset_gate_stall(state: GoalState) -> None:
    state.last_failed_gate_id = ""
    state.same_gate_stall_count = 0


def _infer_supergoal_contract_from_turn(state: GoalState, last_response: str = "") -> bool:
    """Populate the minimal intent contract deterministically when critic is down.

    G1 is a prerequisite for useful autonomous work. It should not depend only
    on an auxiliary critic that can timeout; if the supergoal text exists, the
    runner can at least preserve a conservative intent/success contract and let
    later critic calls refine it.
    """
    if getattr(state, "mode", "goal") != "supergoal":
        return False
    changed = False
    goal_text = " ".join((state.goal or "").split())
    if goal_text and not state.inferred_user_intent:
        state.inferred_user_intent = _truncate(goal_text, 300)
        changed = True
    if not state.success_definition:
        response_low = (last_response or "").lower()
        if any(k in response_low for k in ("verified", "tested", "pytest", "evidence", "artifact", "report")):
            state.success_definition = "produce a verified outcome with concrete tool-backed evidence/artifacts"
        else:
            state.success_definition = "make measurable progress toward the stated supergoal and report evidence, blockers, or a final outcome"
        changed = True
    _update_supergoal_gates(state)
    return changed


def _goal_events_state_changed(state: GoalState, events: List[GoalEvent]) -> bool:
    return _project_events_to_board(
        state,
        events,
        merge_compact_list=_merge_compact_list,
        truncate=_truncate,
        merge_research_findings=_merge_research_findings,
        research_sufficiency_from_findings=_research_sufficiency_from_findings,
        update_gates=_update_supergoal_gates,
    )

def _normalize_loaded_supergoal_state(state: Optional[GoalState]) -> bool:
    """Bring old persisted supergoal states under the current deterministic guards."""
    if state is None or getattr(state, "mode", "goal") != "supergoal":
        return False
    should_backfill_contract = bool(
        state.turns_used > 0
        or state.last_failed_gate_id
        or state.consecutive_critic_failures >= DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES
    )
    changed = _infer_supergoal_contract_from_turn(state, "") if should_backfill_contract else False
    stale_gate_id = state.last_failed_gate_id
    if stale_gate_id:
        gate = next((g for g in state.gates or [] if g.id == stale_gate_id), None)
        if gate is not None and gate.status == "passed":
            _reset_gate_stall(state)
            changed = True
    else:
        # Older states may have stale G1 text in last_reason/next_best_action
        # even after deterministic normalization passes G1.
        for gate in state.gates or []:
            if gate.status == "passed" and (
                f"gate {gate.id} remains open" in (state.last_reason or "")
                or f"Satisfy gate {gate.id}:" in (state.next_best_action or "")
            ):
                stale_gate_id = gate.id
                break
    if stale_gate_id:
        gate = next((g for g in state.gates or [] if g.id == stale_gate_id), None)
        if gate is not None and gate.status == "passed":
            first_failed = _first_failed_gate(state)
            if f"gate {gate.id} remains open" in (state.last_reason or ""):
                if first_failed is not None:
                    state.last_reason = f"supergoal paused after stale gate {gate.id} was normalized; next open gate is {first_failed.id}: {first_failed.description}"
                else:
                    state.last_reason = "supergoal paused after stale gate state was normalized"
                changed = True
            if f"Satisfy gate {gate.id}:" in (state.next_best_action or ""):
                if first_failed is not None:
                    state.next_best_action = f"Satisfy gate {first_failed.id}: {first_failed.description}"
                else:
                    state.next_best_action = "Review normalized supergoal state and decide whether to resume or finalize."
                changed = True
    deterministic_board_progress = bool(
        state.evidence
        or state.research_findings
        or state.action_history
    )
    if (
        state.status == "active"
        and state.consecutive_critic_failures >= DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES
        and not deterministic_board_progress
    ):
        state.status = "paused"
        state.paused_reason = (
            f"supergoal critic/board update failed {state.consecutive_critic_failures} turns in a row"
        )
        state.should_replan = True
        state.next_best_action = state.next_best_action or "Repair auxiliary critic/board update path, then resume with a strategic replan."
        changed = True
    return changed



def _record_supergoal_turn_artifacts(state: GoalState, last_response: str) -> bool:
    """Conservatively mirror visible turn output into board evidence/action state.

    Critic JSON is best-effort. The deterministic state machine still needs a
    minimal audit trail so G3/action_history do not stay empty while the agent
    is actually running commands/tests/reports.
    """
    if getattr(state, "mode", "goal") != "supergoal" or not (last_response or "").strip():
        return False
    changed = False
    normalized = " ".join(last_response.split())
    # Text classification is only a fallback.  Once a structured proposal
    # exists, do not let words like "script/report/validator" overwrite the
    # controller-approved action class.
    action = _classify_action_text(normalized) if getattr(state, "action_proposal", SupergoalActionProposal()).is_empty() else "unknown"
    if action and action != "unknown":
        before = list(state.action_history or [])
        state.action_history = (before + [action])[-12:]
        changed = state.action_history != before or changed
        if state.current_action_class in {"", "unknown"}:
            state.current_action_class = action
            changed = True
    low = normalized.lower()
    evidence_markers = (
        "verified", "tested", "pytest", "test passed", "tests passed", "artifact",
        "evidence", "report", "log", "wrote", "saved", "created", "backtest", "baseline",
        "验证", "测试", "证据", "日志", "报告", "回测",
    )
    if any(marker in low for marker in evidence_markers) or _artifact_paths(normalized):
        before = list(state.evidence or [])
        state.evidence = _merge_compact_list(
            state.evidence,
            [_truncate(normalized, 300)],
            max_items=20,
        )
        changed = state.evidence != before or changed

    research_markers = (
        "research", "survey", "github", "docs", "paper", "benchmark", "scan",
        "news", "rss", "source", "external", "market", "taxonomy", "调研",
        "检索", "外部", "新闻", "来源", "基准", "对比",
    )
    has_evidence_marker = any(marker in low for marker in evidence_markers) or bool(_artifact_paths(normalized))
    if has_evidence_marker and any(marker in low for marker in research_markers):
        source_type = "benchmark" if any(k in low for k in ("benchmark", "baseline", "backtest", "基准", "回测")) else "local"
        if any(k in low for k in ("github", "repo")):
            source_type = "github"
        elif any(k in low for k in ("docs", "paper", "web", "rss", "news", "external", "新闻", "外部")):
            source_type = "web"
        finding = ResearchFinding(
            source_type=source_type,
            title=_truncate(normalized, 120),
            locator="assistant_turn",
            claim=_truncate(normalized, 240),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            tool_call_id="",
            evidence_quote_or_hash=_truncate(normalized, 200),
            evidence_source="assistant_claim",
            trust_level="claim",
        )
        before_findings = list(state.research_findings or [])
        state.research_findings = _merge_research_findings(state.research_findings, finding.to_dict())
        state.research_sufficiency = _research_sufficiency_from_findings(state, state.research_sufficiency)
        changed = state.research_findings != before_findings or changed
    _update_supergoal_gates(state)
    return changed


def _hypothesis_complete(h: HypothesisRecord) -> bool:
    return _gate_hypothesis_complete(h)



def _sync_evidence_layers_from_findings(state: GoalState) -> bool:
    return _gate_sync_evidence_layers_from_findings(state)


def _set_gate_open(gate: GoalGate, *, missing: List[str], reason: str) -> None:
    _gate_set_open(gate, missing=missing, reason=reason)


def _update_supergoal_gates(state: GoalState) -> None:
    _gate_evaluate_gates(
        state,
        default_gate_builder=_default_supergoal_gates,
        ensure_gate_set=_ensure_supergoal_gates_for_text,
    )


def _apply_inertia_guard(state: GoalState) -> None:
    state.hard_gate_reason = ""
    first_failed = _first_blocking_failed_gate(state)
    if not first_failed:
        state.same_action_no_evidence_count = 0
        state.last_action_evidence_count = _gate_eligible_evidence_count(state)
        return

    proposal = state.action_proposal
    if proposal.is_empty():
        proposal = _fallback_action_proposal(state, state.next_best_action)
        state.action_proposal = proposal
    proposed = proposal.action_class or "unknown"
    state.current_action_class = proposed
    changed_action_class = False
    if proposed and proposed != "unknown":
        hist = list(state.action_history or [])
        if not hist or hist[-1] != proposed:
            changed_action_class = True
            hist.append(proposed)
        state.action_history = hist[-12:]

    if proposal.target_gate_id and proposal.target_gate_id != first_failed.id and not proposal.override_reason:
        state.hard_gate_reason = (
            f"action proposal targets {proposal.target_gate_id}, but first failed blocking gate is "
            f"{first_failed.id}: {first_failed.description}"
        )

    if proposed == "infra_engineering":
        first_failed = next((g for g in state.gates if g.id.startswith("SG-") and _is_gate_open(g)), first_failed)

    strategy_gate_open = first_failed.id.startswith("SG-")
    infra_dependency_proof = bool(proposal.override_reason and "depend" in proposal.override_reason.lower())
    if not state.hard_gate_reason and proposed == "infra_engineering" and strategy_gate_open and not infra_dependency_proof:
        state.hard_gate_reason = f"blocked infra_engineering while {first_failed.id} is open: {first_failed.description}"

    recent = state.action_history[-5:]
    infra_streak = len(recent) >= 3 and all(a == "infra_engineering" for a in recent[-3:])
    if not state.hard_gate_reason and infra_streak and any(g.id.startswith("SG-") and _is_gate_open(g) for g in state.gates):
        state.hard_gate_reason = "blocked infra inertia: recent turns are infrastructure while strategy gates remain open"

    evidence_count = _gate_eligible_evidence_count(state)
    if changed_action_class:
        state.same_action_no_evidence_count = 0
    elif evidence_count <= int(state.last_action_evidence_count or 0):
        state.same_action_no_evidence_count += 1
    else:
        state.same_action_no_evidence_count = 0
    state.last_action_evidence_count = evidence_count
    if not state.hard_gate_reason and state.same_action_no_evidence_count >= DEFAULT_MAX_SAME_GATE_STALLS:
        state.hard_gate_reason = (
            f"blocked: gate {first_failed.id} saw no evidence growth for "
            f"{state.same_action_no_evidence_count} consecutive action approvals"
        )
        if first_failed.id.startswith("SG-") and not state.no_edge_report:
            state.no_edge_report = "No evidence growth after repeated attempts; require new hypothesis family or no-edge attribution before continuing."

    if state.hard_gate_reason:
        state.should_replan = True
        state.replan_count += 1
        proposal.target_gate_id = first_failed.id
        proposal.expected_evidence = proposal.expected_evidence or [first_failed.description]
        proposal.stop_if = proposal.stop_if or ["evidence does not increase after this turn"]
        if first_failed.id == "SG-1":
            state.next_best_action = "Generate a 3-item hypothesis portfolio with baseline, experiment design, kill criteria, and expected edge; do not build more infrastructure."
            state.action_proposal = SupergoalActionProposal(
                action_class="hypothesis_generation",
                target_gate_id="SG-1",
                expected_evidence=["3 strategy hypotheses"],
                tools_needed=[],
                max_turn_budget=1,
                risk_level="low",
                why_this_gate_first="SG-1 is the first failed blocking gate",
                stop_if=["hypothesis portfolio is still below 3 items"],
                text=state.next_best_action,
            )
        elif first_failed.id == "SG-2":
            state.next_best_action = "Execute or verify the open hypotheses against their baselines and acceptance criteria; do not build more infrastructure unless it is a proven dependency."
            state.action_proposal = SupergoalActionProposal(
                action_class="experiment_execution",
                target_gate_id="SG-2",
                expected_evidence=["baseline", "experiment artifact", "verdict"],
                tools_needed=["terminal", "read_file"],
                max_turn_budget=1,
                risk_level="medium",
                why_this_gate_first="SG-2 is the first failed blocking gate",
                stop_if=["no artifact/verdict is produced"],
                text=state.next_best_action,
            )
        elif first_failed.id == "SG-3":
            state.next_best_action = "Produce a no-edge attribution report or identify the next hypothesis family; do not add more validators/checkers."
            state.action_proposal = SupergoalActionProposal(
                action_class="reporting",
                target_gate_id="SG-3",
                expected_evidence=["no_edge_report or passed hypothesis"],
                tools_needed=["read_file"],
                max_turn_budget=1,
                risk_level="low",
                why_this_gate_first="SG-3 is the first failed blocking gate",
                stop_if=["no attribution report is produced"],
                text=state.next_best_action,
            )


def _default_supergoal_plan() -> List[PlanStep]:
    """Conservative starter plan for long-running goals.

    The LLM can refine this via future plan events, but a deterministic skeleton
    makes status, audit, and continuation prompts immediately structured.
    """
    return [
        PlanStep("S1", "Infer root intent and success definition beyond the literal request", "in_progress", "Root intent, constraints, and acceptance criteria recorded"),
        PlanStep("S2", "Model the problem from first principles", "pending", "Load-bearing truths, dependencies, and failure modes recorded"),
        PlanStep("S3", "Scan existing solutions before building", "pending", "Mature tools/APIs/projects/patterns considered with build-vs-reuse decision"),
        PlanStep("S4", "Execute the shortest reliable concrete path", "pending", "Tool-backed action completed and summarized"),
        PlanStep("S5", "Verify results against root intent and acceptance criteria", "pending", "Commands/tests/files/logs or cited sources prove the result"),
        PlanStep("S6", "Finalize, report artifacts, and extract reusable lessons", "pending", "User-facing summary plus skill/memory candidate if useful"),
    ]


def _current_or_next_step(state: GoalState) -> Optional[PlanStep]:
    if not state.plan_steps:
        return None
    if state.current_step_id:
        for step in state.plan_steps:
            if step.id == state.current_step_id and step.status not in {"done", "skipped"}:
                return step
    for step in state.plan_steps:
        if step.status in {"pending", "in_progress", "failed", "blocked"}:
            return step
    return None


def _ensure_current_step(state: GoalState) -> None:
    step = _current_or_next_step(state)
    if step is None:
        state.current_step_id = ""
        return
    state.current_step_id = step.id
    if step.status == "pending":
        step.status = "in_progress"


def _update_supergoal_plan_from_progress(state: GoalState, *, verdict: str) -> None:
    """Maintain plan by deterministic gate/artifact verification, not progress prose."""
    if getattr(state, "mode", "goal") != "supergoal" or not state.plan_steps:
        return
    _update_supergoal_gates(state)
    _ensure_current_step(state)
    current = _current_or_next_step(state)
    if current is None:
        return
    if verdict == "done":
        current.status = "done"
        current.summary = state.last_reason or "Goal completed"
        for step in state.plan_steps:
            if step.status in {"pending", "in_progress"}:
                step.status = "done"
        for gate in state.gates:
            if gate.status != "passed":
                if _is_blocking_gate(gate):
                    gate.status = "passed"
                    gate.evidence = gate.evidence or "completion judge marked goal done"
                else:
                    gate.status = "followup"
                    gate.evidence = ""
        state.current_step_id = ""
        return
    if state.strategy_health == "blocked":
        current.status = "blocked"
        current.summary = state.last_reason or state.next_best_action
        return
    if state.should_replan or state.hard_gate_reason:
        current.status = "in_progress"
        current.summary = state.hard_gate_reason or "Replan requested before continuing this step"
        return

    first_failed = _first_failed_gate(state)
    if first_failed is not None:
        current.status = "in_progress"
        current.summary = f"Waiting on gate {first_failed.id}: {first_failed.description}"
        return

    # Only when all gates pass may ordinary real progress advance the current step.
    if state.progress == "real" and current.status == "in_progress":
        current.status = "done"
        current.summary = state.last_reason or state.next_best_action
        _ensure_current_step(state)


_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _auxiliary_task_config(task: str) -> Dict[str, Any]:
    """Return raw ``auxiliary.<task>`` config from config.yaml.

    Kept local to goals.py so /supergoal can distinguish an explicitly
    configured critic route from the completion judge route without expanding
    the public auxiliary-client API.
    """
    if not task:
        return {}
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        aux = cfg.get("auxiliary") or {}
        raw = aux.get(task) if isinstance(aux, dict) else None
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _auxiliary_int_config(task: str, key: str, default: int) -> int:
    """Resolve a positive integer from auxiliary.<task>.<key>."""
    try:
        value = int(_auxiliary_task_config(task).get(key, default))
        if value > 0:
            return value
    except Exception:
        pass
    return default


def _auxiliary_float_config(task: str, key: str, default: float) -> float:
    """Resolve a positive float from auxiliary.<task>.<key>."""
    try:
        value = float(_auxiliary_task_config(task).get(key, default))
        if value > 0:
            return value
    except Exception:
        pass
    return default


def _goal_judge_max_tokens() -> int:
    """Resolve auxiliary.goal_judge.max_tokens, falling back to the default.

    ``load_config()`` is cached on the config file's (mtime, size), so calling
    this once per judge turn is cheap. A non-positive or non-int value falls
    back to the default rather than crashing the goal loop.
    """
    return _auxiliary_int_config("goal_judge", "max_tokens", DEFAULT_JUDGE_MAX_TOKENS)


def _supergoal_critic_max_tokens() -> int:
    """Resolve auxiliary.supergoal_critic.max_tokens for board-update JSON."""
    return _auxiliary_int_config(
        "supergoal_critic", "max_tokens", DEFAULT_SUPERGOAL_CRITIC_MAX_TOKENS
    )


def _supergoal_critic_timeout(default: float = DEFAULT_JUDGE_TIMEOUT) -> float:
    """Resolve auxiliary.supergoal_critic.timeout for the critic call."""
    return _auxiliary_float_config("supergoal_critic", "timeout", default)


def _supergoal_replan_interval() -> int:
    """Resolve goals.super_replan_interval. 0 disables periodic replans."""
    try:
        from hermes_cli.config import load_config

        value = ((load_config() or {}).get("goals") or {}).get("super_replan_interval", 5)
        value = int(value)
        return max(0, value)
    except Exception:
        return 5


def _supergoal_permission_defaults() -> tuple[str, Dict[str, Any]]:
    try:
        from hermes_cli.config import load_config

        goals_cfg = ((load_config() or {}).get("goals") or {})
        mode = str(goals_cfg.get("supergoal_permission_mode") or "supervised").strip().lower()
        if mode not in {"supervised", "full_auto"}:
            mode = "supervised"
        contract = goals_cfg.get("supergoal_permission_contract")
        return mode, contract if isinstance(contract, dict) else {}
    except Exception:
        return "supervised", {}


def _parse_judge_response(raw: str) -> Tuple[bool, str, bool]:
    """Parse the judge's reply. Fail-open to ``(False, "<reason>", parse_failed)``.

    Returns ``(done, reason, parse_failed)``. ``parse_failed`` is True when the
    judge returned output that couldn't be interpreted as the expected JSON
    verdict (empty body, prose, malformed JSON). Callers use that flag to
    auto-pause after N consecutive parse failures so a weak judge model
    doesn't silently burn the turn budget.
    """
    if not raw:
        return False, "judge returned empty response", True

    text = raw.strip()

    # Strip markdown code fences the model may wrap JSON in.
    if text.startswith("```"):
        text = text.strip("`")
        # Peel off leading json/JSON/etc tag
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]

    # First try: parse the whole blob.
    data: Optional[Dict[str, Any]] = None
    try:
        data = json.loads(text)
    except Exception:
        # Second try: pull the first JSON object out.
        match = _JSON_OBJECT_RE.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                data = None

    if not isinstance(data, dict):
        return False, f"judge reply was not JSON: {_truncate(raw, 200)!r}", True

    done_val = data.get("done")
    if isinstance(done_val, str):
        done = done_val.strip().lower() in {"true", "yes", "1", "done"}
    else:
        done = bool(done_val)
    reason = str(data.get("reason") or "").strip()
    if not reason:
        reason = "no reason provided"
    return done, reason, False


def judge_goal(
    goal: str,
    last_response: str,
    *,
    timeout: float = DEFAULT_JUDGE_TIMEOUT,
    subgoals: Optional[List[str]] = None,
) -> Tuple[str, str, bool]:
    """Ask the auxiliary model whether the goal is satisfied.

    Returns ``(verdict, reason, parse_failed)`` where verdict is ``"done"``,
    ``"continue"``, or ``"skipped"`` (when the judge couldn't be reached).

    ``parse_failed`` is True only when the judge call succeeded but its output
    was unusable (empty or non-JSON). API/transport errors return False — they
    are transient and should fail-open silently. Callers use this flag to
    auto-pause after N consecutive parse failures (see
    ``DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES``).

    ``subgoals`` is an optional list of user-added criteria (from
    ``/subgoal``) that the judge must also factor into its DONE/CONTINUE
    decision. When non-empty the prompt switches to the with-subgoals
    template; otherwise behavior is identical to the original judge.

    This is deliberately fail-open: any error returns ``("continue", "...", False)``
    so a broken judge doesn't wedge progress — the turn budget and the
    consecutive-parse-failures auto-pause are the backstops.
    """
    if not goal.strip():
        return "skipped", "empty goal", False
    if not last_response.strip():
        # No substantive reply this turn — almost certainly not done yet.
        return "continue", "empty response (nothing to evaluate)", False

    try:
        from agent.auxiliary_client import get_auxiliary_extra_body, get_text_auxiliary_client
    except Exception as exc:
        logger.debug("goal judge: auxiliary client import failed: %s", exc)
        return "continue", "auxiliary client unavailable", False

    try:
        client, model = get_text_auxiliary_client("goal_judge")
    except Exception as exc:
        logger.debug("goal judge: get_text_auxiliary_client failed: %s", exc)
        return "continue", "auxiliary client unavailable", False

    if client is None or not model:
        return "continue", "no auxiliary client configured", False

    # Build the prompt — pick the with-subgoals variant when applicable.
    clean_subgoals = [s.strip() for s in (subgoals or []) if s and s.strip()]
    current_time = datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    if clean_subgoals:
        subgoals_block = "\n".join(
            f"- {i}. {text}" for i, text in enumerate(clean_subgoals, start=1)
        )
        prompt = JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            subgoals_block=_truncate(subgoals_block, 2000),
            response=_truncate(last_response, _JUDGE_RESPONSE_SNIPPET_CHARS),
            current_time=current_time,
        )
    else:
        prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            response=_truncate(last_response, _JUDGE_RESPONSE_SNIPPET_CHARS),
            current_time=current_time,
        )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=_goal_judge_max_tokens(),
            timeout=timeout,
            extra_body=get_auxiliary_extra_body() or None,
        )
    except Exception as exc:
        logger.info("goal judge: API call failed (%s) — falling through to continue", exc)
        return "continue", f"judge error: {type(exc).__name__}", False

    try:
        raw = resp.choices[0].message.content or ""
    except Exception:
        raw = ""

    done, reason, parse_failed = _parse_judge_response(raw)
    verdict = "done" if done else "continue"
    logger.info("goal judge: verdict=%s reason=%s", verdict, _truncate(reason, 120))
    return verdict, reason, parse_failed


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except Exception:
            return None


def critic_supergoal(state: GoalState, last_response: str, *, timeout: float = DEFAULT_JUDGE_TIMEOUT) -> Optional[Dict[str, Any]]:
    """Ask an auxiliary critic to update /supergoal working memory.

    Fail-closed for orchestration: any error returns None and the normal
    done/continue judge still drives the loop. This critic improves strategy
    quality but must never wedge progress.
    """
    if not state or getattr(state, "mode", "goal") != "supergoal" or not last_response.strip():
        return None
    try:
        from agent.auxiliary_client import get_auxiliary_extra_body, get_text_auxiliary_client
    except Exception as exc:
        logger.debug("supergoal critic: auxiliary client import failed: %s", exc)
        return None

    # Prefer a dedicated supergoal_critic route when configured, otherwise
    # fall back to goal_judge for backwards compatibility. This lets deployments
    # keep the main model strong while routing board-update JSON to a faster CPA
    # model such as gpt-5.4-mini without changing the completion judge.
    critic_config = _auxiliary_task_config("supergoal_critic")
    has_dedicated_route = any(
        str(critic_config.get(key) or "").strip()
        for key in ("provider", "model", "base_url", "api_key", "api_mode")
    )
    task_name = "supergoal_critic" if has_dedicated_route else "goal_judge"
    try:
        client, model = get_text_auxiliary_client(task_name)
    except Exception as exc:
        logger.debug("supergoal critic: %s client unavailable: %s", task_name, exc)
        return None

    if client is None or not model:
        return None

    # Dedicated critic config can shrink prompt slices for latency-sensitive
    # CPA/custom routes without weakening the main supergoal continuation.
    board_chars = _auxiliary_int_config("supergoal_critic", "board_chars", 2400)
    response_chars = _auxiliary_int_config("supergoal_critic", "response_chars", 2400)
    goal_chars = _auxiliary_int_config("supergoal_critic", "goal_chars", 1200)
    prompt = SUPERGOAL_CRITIC_USER_PROMPT_TEMPLATE.format(
        goal=_truncate(state.goal, goal_chars),
        board=_truncate(state.render_supergoal_board(), board_chars),
        response=_truncate(last_response, response_chars),
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUPERGOAL_CRITIC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=_supergoal_critic_max_tokens(),
            timeout=_supergoal_critic_timeout(timeout),
            extra_body=get_auxiliary_extra_body() or None,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.info("supergoal critic: API call failed (%s) — skipping", exc)
        return None

    data = _parse_json_object(raw)
    if data is None:
        logger.info("supergoal critic: reply was not JSON: %r", _truncate(raw, 200))
        return None
    return data


def apply_supergoal_critic(state: GoalState, data: Optional[Dict[str, Any]]) -> None:
    """Merge critic output into the persistent Supergoal State Board."""
    if not state or not data:
        return

    intent = str(data.get("inferred_user_intent") or "").strip()
    if intent:
        state.inferred_user_intent = _truncate(" ".join(intent.split()), 300)
    success = str(data.get("success_definition") or "").strip()
    if success:
        state.success_definition = _truncate(" ".join(success.split()), 300)
    state.first_principles_model = _merge_compact_list(
        state.first_principles_model, data.get("first_principles_model"), max_items=16
    )
    state.existing_solution_scan = _merge_compact_list(
        state.existing_solution_scan, data.get("existing_solution_scan"), max_items=16
    )
    reuse_decision = str(data.get("build_vs_reuse_decision") or "").strip()
    if reuse_decision:
        state.build_vs_reuse_decision = _truncate(" ".join(reuse_decision.split()), 300)
    literalism = str(data.get("literalism_risk") or "").strip().lower()
    if literalism in {"low", "medium", "high"}:
        state.literalism_risk = literalism
    research = str(data.get("research_sufficiency") or "").strip().lower()
    if research not in {"sufficient", "thin", "missing"}:
        research = ""
    state.research_findings = _merge_research_findings(
        state.research_findings, data.get("research_findings")
    )
    state.research_sufficiency = _research_sufficiency_from_findings(state, research)
    state.hypothesis_portfolio = _merge_hypothesis_portfolio(
        state.hypothesis_portfolio, data.get("hypothesis_portfolio") or data.get("new_hypotheses")
    )
    no_edge = str(data.get("no_edge_report") or "").strip()
    if no_edge:
        state.no_edge_report = _truncate(" ".join(no_edge.split()), 500)
    # Critic output can itself satisfy the currently open gate (e.g. add the
    # SG-1 hypothesis portfolio). Refresh deterministic gates before defaulting
    # action_proposal.target_gate_id so the proposal targets the next true
    # blocking gate, not stale pre-merge gate state.
    _update_supergoal_gates(state)
    proposal = _proposal_from_critic_data(state, data)
    state.action_proposal = proposal
    state.current_action_class = proposal.action_class or "unknown"

    progress = str(data.get("progress") or "").strip().lower()
    if progress in {"real", "weak", "none", "regressed"}:
        state.progress = progress
    health = str(data.get("strategy_health") or "").strip().lower()
    if health in {"good", "stuck", "drifting", "repeating", "premature", "blocked"}:
        state.strategy_health = health
    if "root_cause_confidence" in data:
        state.root_cause_confidence = _coerce_float(
            data.get("root_cause_confidence"), state.root_cause_confidence
        )
    explicit_replan = bool(data.get("should_replan", False))
    quality_replan = state.literalism_risk == "high" or state.research_sufficiency in {"thin", "missing"}
    state.should_replan = explicit_replan or quality_replan or state.strategy_health in {
        "stuck", "drifting", "repeating", "premature", "blocked"
    } or state.progress in {"none", "regressed"}
    if state.should_replan:
        state.replan_count += 1
    nba = proposal.text or str(data.get("next_best_action") or "").strip()
    if nba:
        state.next_best_action = _truncate(" ".join(nba.split()), 300)

    state.milestones = _merge_compact_list(state.milestones, data.get("new_milestones"))
    state.hypotheses = _merge_compact_list(state.hypotheses, data.get("new_hypotheses"))
    state.evidence = _merge_compact_list(state.evidence, data.get("new_evidence"))
    state.attempted_solutions = _merge_compact_list(
        state.attempted_solutions, data.get("new_attempted_solutions")
    )
    state.blockers = _merge_compact_list(state.blockers, data.get("new_blockers"))
    missing = _clean_string_list(data.get("missing_evidence"), limit=8)
    if missing:
        state.risks = _merge_compact_list(
            state.risks,
            [f"missing evidence: {item}" for item in missing],
            max_items=20,
        )
    state.risks = _merge_compact_list(state.risks, data.get("new_risks"))
    if state.literalism_risk == "high":
        state.risks = _merge_compact_list(
            state.risks,
            ["literalism risk: agent may be following the written task without satisfying root intent"],
            max_items=20,
        )
    if state.research_sufficiency in {"thin", "missing"}:
        state.risks = _merge_compact_list(
            state.risks,
            [f"tool-backed research ledger is {state.research_sufficiency}"],
            max_items=20,
        )
    _update_supergoal_gates(state)
    _apply_inertia_guard(state)


# ──────────────────────────────────────────────────────────────────────
# GoalManager — the orchestration surface CLI + gateway talk to
# ──────────────────────────────────────────────────────────────────────


class GoalManager:
    """Per-session goal state + continuation decisions.

    The CLI and gateway each hold one ``GoalManager`` per live session.

    Methods:

    - ``set(goal)`` — start a new standing goal.
    - ``clear()`` — remove the active goal.
    - ``pause()`` / ``resume()`` — explicit user controls.
    - ``status()`` — printable one-liner.
    - ``evaluate_after_turn(last_response)`` — call the judge, update state,
      and return a decision dict the caller uses to drive the next turn.
    - ``next_continuation_prompt()`` — the canonical user-role message to
      feed back into ``run_conversation``.
    """

    def __init__(self, session_id: str, *, default_max_turns: int = DEFAULT_MAX_TURNS):
        self.session_id = session_id
        self.default_max_turns = int(default_max_turns or DEFAULT_MAX_TURNS)
        self._state: Optional[GoalState] = load_goal(session_id)
        if self._state is not None:
            changed = _goal_events_state_changed(
                self._state, load_goal_events(self.session_id, limit=200)
            )
            changed = _normalize_loaded_supergoal_state(self._state) or changed
            if changed:
                save_goal(self.session_id, self._state)

    def _record_event(
        self,
        event_type: str,
        *,
        summary: str = "",
        data: Optional[Dict[str, Any]] = None,
    ) -> Optional[GoalEvent]:
        """Append a durable audit event and mirror counters into local state."""
        turn = self._state.turns_used if self._state else 0
        event = append_goal_event(
            self.session_id,
            event_type,
            turn=turn,
            summary=summary,
            data=data or {},
        )
        if event is not None and self._state is not None:
            # append_goal_event updates the persisted state from disk; mirror
            # counters onto this in-memory object and save it so later local
            # mutations do not accidentally revert event metadata.
            self._state.event_count = len(load_goal_events(self.session_id, limit=200))
            self._state.last_event_type = event.type
            save_goal(self.session_id, self._state)
        return event

    def recent_events(self, *, limit: int = 20) -> List[GoalEvent]:
        return load_goal_events(self.session_id, limit=limit)

    # --- introspection ------------------------------------------------

    @property
    def state(self) -> Optional[GoalState]:
        return self._state

    def is_active(self) -> bool:
        return self._state is not None and self._state.status == "active"

    def has_goal(self) -> bool:
        return self._state is not None and self._state.status in {"active", "paused"}

    def status_line(self, *, compact: bool = False) -> str:
        s = self._state
        if s is None or s.status in {"cleared",}:
            return "No active goal. Set one with /goal <text>."
        turns = f"{s.turns_used}/{s.max_turns} turns"
        label = "Supergoal" if getattr(s, "mode", "goal") == "supergoal" else "Goal"
        if compact:
            command = "/supergoal" if label == "Supergoal" else "/goal"
            lines = [f"{label} {s.status} · turns {s.turns_used}/{s.max_turns}"]
            if s.last_verdict or s.last_reason:
                verdict = s.last_verdict or "last"
                reason = f": {_truncate(s.last_reason, 96)}" if s.last_reason else ""
                lines.append(f"last {verdict}{reason}")
            if getattr(s, "mode", "goal") == "supergoal":
                if getattr(s, "same_gate_stall_count", 0) and getattr(s, "last_failed_gate_id", ""):
                    lines.append(f"gate_stall {s.last_failed_gate_id}:{s.same_gate_stall_count}")
                elif getattr(s, "consecutive_critic_failures", 0):
                    lines.append(f"critic_failures {s.consecutive_critic_failures}")
                else:
                    first_gate = _first_failed_gate(s)
                    if first_gate is not None:
                        lines.append(f"first_gate {first_gate.id}: {_truncate(first_gate.description, 96)}")
                if getattr(s, "next_best_action", ""):
                    lines.append(f"next {_truncate(s.next_best_action, 96)}")
            lines.append(f"Controls: {command} pause · {command} status · {command} clear")
            return "\n".join(lines)
        sub = f", {len(s.subgoals)} subgoal{'s' if len(s.subgoals) != 1 else ''}" if s.subgoals else ""
        diagnostics = []
        if s.last_verdict:
            diagnostics.append(f"last={s.last_verdict}")
        if s.last_turn_at:
            diagnostics.append(
                "turn_at=" + datetime.fromtimestamp(s.last_turn_at, tz=timezone.utc).strftime("%m-%d %H:%MZ")
            )
        if s.last_continuation_enqueued_at:
            diagnostics.append(
                "queued=" + datetime.fromtimestamp(s.last_continuation_enqueued_at, tz=timezone.utc).strftime("%m-%d %H:%MZ")
            )
        if s.last_continuation_kind:
            diagnostics.append(f"kind={s.last_continuation_kind}")
        if getattr(s, "mode", "goal") == "supergoal":
            if getattr(s, "progress", "unknown") != "unknown":
                diagnostics.append(f"progress={s.progress}")
            if getattr(s, "strategy_health", "unknown") != "unknown":
                diagnostics.append(f"strategy={s.strategy_health}")
            if getattr(s, "literalism_risk", "unknown") != "unknown":
                diagnostics.append(f"literalism={s.literalism_risk}")
            if getattr(s, "research_sufficiency", "unknown") != "unknown":
                diagnostics.append(f"research={s.research_sufficiency}")
            if getattr(s, "should_replan", False):
                diagnostics.append("replan=pending")
            if getattr(s, "consecutive_critic_failures", 0):
                diagnostics.append(f"critic_failures={s.consecutive_critic_failures}")
            if getattr(s, "same_gate_stall_count", 0) and getattr(s, "last_failed_gate_id", ""):
                diagnostics.append(f"gate_stall={s.last_failed_gate_id}:{s.same_gate_stall_count}")
            if getattr(s, "next_best_action", ""):
                diagnostics.append(f"next={_truncate(s.next_best_action, 80)}")
            current = _current_or_next_step(s)
            if current is not None:
                diagnostics.append(f"step={current.id}:{current.status}")
            if getattr(s, "gates", None):
                passed = sum(1 for g in s.gates if g.status == "passed")
                diagnostics.append(f"gates={passed}/{len(s.gates)}")
                first_gate = _first_failed_gate(s)
                if first_gate is not None:
                    diagnostics.append(f"first_gate={first_gate.id}")
            if getattr(s, "hard_gate_reason", ""):
                diagnostics.append(f"hard_gate={_truncate(s.hard_gate_reason, 80)}")
            if getattr(s, "event_count", 0):
                diagnostics.append(f"events={s.event_count}")
        diag = f"; {'; '.join(diagnostics)}" if diagnostics else ""
        reason = f" — {s.last_reason}" if s.last_reason else ""
        if s.status == "active":
            return f"⊙ {label} (active, {turns}{sub}{diag}): {s.goal}{reason}"
        if s.status == "paused":
            extra = f" — {s.paused_reason}" if s.paused_reason else ""
            return f"⏸ {label} (paused, {turns}{sub}{diag}{extra}): {s.goal}{reason}"
        if s.status == "done":
            return f"✓ {label} done ({turns}{sub}{diag}): {s.goal}{reason}"
        return f"{label} ({s.status}, {turns}{sub}{diag}): {s.goal}{reason}"

    def status_card(self, *, include_debug: bool = False) -> GoalStatusCard:
        """Return a platform-neutral goal status card plus text fallback."""
        return build_goal_status_card(
            self._state,
            session_id=self.session_id,
            compact_text=self.status_line(compact=True),
            include_debug=include_debug,
        )

    def get_status_card_message_id(self, platform_key: str) -> str:
        if not self._state:
            return ""
        return (self._state.status_card_message_ids or {}).get(str(platform_key), "")

    def set_status_card_message_id(self, platform_key: str, message_id: str) -> None:
        if not self._state:
            return
        key = str(platform_key or "").strip()
        mid = str(message_id or "").strip()
        if not key or not mid:
            return
        self._state.status_card_message_ids[key] = mid
        save_goal(self.session_id, self._state)

    def clear_status_card_message_id(self, platform_key: str) -> None:
        if not self._state:
            return
        key = str(platform_key or "").strip()
        if key and key in self._state.status_card_message_ids:
            self._state.status_card_message_ids.pop(key, None)
            save_goal(self.session_id, self._state)

    # --- mutation -----------------------------------------------------

    def set(self, goal: str, *, max_turns: Optional[int] = None, mode: str = "goal") -> GoalState:
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal text is empty")
        normalized_mode = "supergoal" if mode == "supergoal" else "goal"
        permission_mode, permission_contract = _supergoal_permission_defaults() if normalized_mode == "supergoal" else ("supervised", {})
        state = GoalState(
            goal=goal,
            mode=normalized_mode,
            status="active",
            turns_used=0,
            max_turns=int(max_turns) if max_turns else self.default_max_turns,
            created_at=time.time(),
            last_turn_at=0.0,
            plan_steps=_default_supergoal_plan() if normalized_mode == "supergoal" else [],
            current_step_id="S1" if normalized_mode == "supergoal" else "",
            permission_mode=permission_mode,
            permission_contract=permission_contract,
            gates=_default_supergoal_gates(goal) if normalized_mode == "supergoal" else [],
        )
        self._state = state
        save_goal(self.session_id, state)
        self._record_event(
            "goal_set",
            summary=goal,
            data={"mode": normalized_mode, "max_turns": state.max_turns},
        )
        if normalized_mode == "supergoal":
            self._record_event(
                "plan_created",
                summary="Default supergoal plan initialized",
                data={"steps": [step.to_dict() for step in state.plan_steps]},
            )
        return state

    def pause(self, reason: str = "user-paused") -> Optional[GoalState]:
        if not self._state:
            return None
        self._state.status = "paused"
        self._state.paused_reason = reason
        save_goal(self.session_id, self._state)
        self._record_event("paused", summary=reason, data={"reason": reason})
        return self._state

    def resume(self, *, reset_budget: bool = True) -> Optional[GoalState]:
        if not self._state:
            return None
        self._state.status = "active"
        self._state.paused_reason = None
        if reset_budget:
            self._state.turns_used = 0
        # Explicit resume is a user decision to retry after a pause. Reset
        # consecutive failure latches here; otherwise save/record_event paths
        # reload the state, _normalize_loaded_supergoal_state() immediately
        # re-pauses supergoals with stale critic_failures>=threshold, and the
        # freshly queued continuation is then dropped as "stale" before the
        # agent can run.
        self._state.consecutive_critic_failures = 0
        self._state.consecutive_judge_api_failures = 0
        self._state.consecutive_parse_failures = 0
        # Older supergoal states created before Mission Control gates existed
        # can be resumed after a gateway restart. Backfill gates here so the
        # resumed loop is governed by the current deterministic runtime rather
        # than appearing active while missing hard gates.
        backfilled_gates = False
        if self._state.mode == "supergoal" and not self._state.gates:
            self._state.gates = _default_supergoal_gates(self._state.goal)
            backfilled_gates = True
        save_goal(self.session_id, self._state)
        self._record_event(
            "resumed",
            summary="goal resumed",
            data={"reset_budget": reset_budget, "backfilled_gates": backfilled_gates},
        )
        return self._state

    def clear(self) -> None:
        if self._state is None:
            return
        self._state.status = "cleared"
        save_goal(self.session_id, self._state)
        self._record_event("cleared", summary="goal cleared")
        self._state = None

    def mark_done(self, reason: str) -> None:
        if not self._state:
            return
        self._state.status = "done"
        self._state.last_verdict = "done"
        self._state.last_reason = reason
        _update_supergoal_plan_from_progress(self._state, verdict="done")
        save_goal(self.session_id, self._state)
        self._record_event("done", summary=reason, data={"reason": reason})

    def record_continuation_enqueued(self, *, kind: str = "fifo") -> Optional[GoalState]:
        """Record that the gateway/CLI queued a synthetic continuation turn.

        This is observability-only state; it lets `/goal status` and logs show
        whether the loop actually progressed past judge evaluation into a
        queued continuation.  It deliberately does not change `turns_used` —
        that is counted only after a turn completes.
        """
        if not self._state:
            return None
        self._state.last_continuation_enqueued_at = time.time()
        self._state.last_continuation_kind = kind
        save_goal(self.session_id, self._state)
        self._record_event("continuation_enqueued", summary=kind, data={"kind": kind})
        return self._state

    # --- /subgoal user controls ---------------------------------------

    def add_subgoal(self, text: str) -> str:
        """Append a user-added criterion to the active goal. Requires
        ``has_goal()``; raises ``RuntimeError`` otherwise.

        Returns the cleaned text so the caller can show it back to the user.
        """
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        text = (text or "").strip()
        if not text:
            raise ValueError("subgoal text is empty")
        self._state.subgoals.append(text)
        _ensure_supergoal_gates_for_text(self._state, text)
        save_goal(self.session_id, self._state)
        return text

    def remove_subgoal(self, index_1based: int) -> str:
        """Remove a subgoal by 1-based index. Returns the removed text."""
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        idx = int(index_1based) - 1
        if idx < 0 or idx >= len(self._state.subgoals):
            raise IndexError(
                f"index out of range (1..{len(self._state.subgoals)})"
            )
        removed = self._state.subgoals.pop(idx)
        save_goal(self.session_id, self._state)
        return removed

    def clear_subgoals(self) -> int:
        """Wipe all subgoals. Returns the previous count."""
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        prev = len(self._state.subgoals)
        self._state.subgoals = []
        save_goal(self.session_id, self._state)
        return prev

    def request_replan(self, reason: str = "user-requested") -> Optional[GoalState]:
        if self._state is None or not self.has_goal():
            return None
        if self._state.mode != "supergoal":
            return self._state
        self._state.should_replan = True
        self._state.next_best_action = self._state.next_best_action or "Run a strategic replan before the next concrete step."
        self._state.replan_count += 1
        save_goal(self.session_id, self._state)
        self._record_event("replan_requested", summary=reason, data={"reason": reason})
        return self._state

    def render_subgoals(self) -> str:
        """Public helper for the /subgoal slash command."""
        if self._state is None:
            return "(no active goal)"
        if not self._state.subgoals:
            return "(no subgoals — use /subgoal <text> to add criteria)"
        return self._state.render_subgoals_block()

    # --- the main entry point called after every turn -----------------

    def evaluate_after_turn(
        self,
        last_response: str,
        *,
        user_initiated: bool = True,
    ) -> Dict[str, Any]:
        """Public facade: delegate post-turn control to the runtime controller."""
        if self._state is not None and getattr(self._state, "mode", "goal") == "supergoal":
            from hermes_cli.supergoal.controller import SupergoalController
            from hermes_cli.supergoal.domain import TurnContext
            from hermes_cli.supergoal.evaluators import CompletionJudge, EvaluatorSuite, StrategicCritic

            controller = SupergoalController(
                evaluators=EvaluatorSuite(
                    completion_judge=CompletionJudge(judge_goal),
                    strategic_critic=StrategicCritic(critic_supergoal, apply_supergoal_critic),
                ),
                legacy_decider=self._evaluate_after_turn_legacy,
            )
            return controller.decide_after_turn(
                TurnContext(
                    session_id=self.session_id,
                    state=self._state,
                    last_response=last_response,
                    user_initiated=user_initiated,
                )
            ).to_dict()
        return self._evaluate_after_turn_legacy(last_response, user_initiated=user_initiated)

    def _evaluate_after_turn_legacy(
        self,
        last_response: str,
        *,
        user_initiated: bool = True,
    ) -> Dict[str, Any]:
        """Run the judge and update state. Return a decision dict.

        ``user_initiated`` distinguishes a real user prompt (True) from a
        continuation prompt we fed ourselves (False). Both increment
        ``turns_used`` because both consume model budget.

        Decision keys:
          - ``status``: current goal status after update
          - ``should_continue``: bool — caller should fire another turn
          - ``continuation_prompt``: str or None
          - ``verdict``: "done" | "continue" | "skipped" | "inactive"
          - ``reason``: str
          - ``message``: user-visible one-liner to print/send
        """
        state = self._state
        if state is None or state.status != "active":
            return {
                "status": state.status if state else None,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "inactive",
                "reason": "no active goal",
                "message": "",
            }

        # Count the turn that just finished.
        state.turns_used += 1
        state.last_turn_at = time.time()

        verdict, reason, parse_failed = judge_goal(
            state.goal, last_response, subgoals=state.subgoals or None
        )
        state.last_verdict = verdict
        state.last_reason = reason

        if getattr(state, "mode", "goal") == "supergoal":
            gate_ids_before_critic = _passed_gate_ids(state)
            _infer_supergoal_contract_from_turn(state, last_response)
            _record_supergoal_turn_artifacts(state, last_response)
            for event_type, summary, data in _extract_supergoal_observation_events(last_response):
                self._record_event(event_type, summary=summary, data=data)
            _goal_events_state_changed(state, self.recent_events(limit=200))
            try:
                critic_data = critic_supergoal(state, last_response)
                if critic_data:
                    apply_supergoal_critic(state, critic_data)
                    state.consecutive_critic_failures = 0
                    if _passed_gate_ids(state) != gate_ids_before_critic:
                        _reset_gate_stall(state)
                    interval = _supergoal_replan_interval()
                    if interval and state.turns_used > 0 and state.turns_used % interval == 0:
                        state.should_replan = True
                        state.replan_count += 1
                        if not state.next_best_action:
                            state.next_best_action = "Run a strategic replan before the next concrete step."
                    if verdict == "done":
                        # Do not let the completion judge mark every supergoal gate
                        # passed before the deterministic gate override below gets
                        # a chance to inspect open gates.
                        _update_supergoal_gates(state)
                    else:
                        _update_supergoal_plan_from_progress(state, verdict=verdict)
                else:
                    # If the strict judge API itself is down, don't also count
                    # the optional critic path as a board failure; the separate
                    # judge_api_failures guard owns that fail-closed path.
                    if not (reason and reason.startswith("judge error:")):
                        state.consecutive_critic_failures += 1
                    if verdict == "done":
                        _update_supergoal_gates(state)
                    else:
                        _update_supergoal_plan_from_progress(state, verdict=verdict)
                self._record_event(
                    "critic",
                    summary=f"progress={state.progress}; strategy={state.strategy_health}; replan={state.should_replan}",
                    data={
                        "progress": state.progress,
                        "strategy_health": state.strategy_health,
                        "root_cause_confidence": state.root_cause_confidence,
                        "should_replan": state.should_replan,
                        "next_best_action": state.next_best_action,
                        "critic_failed": not bool(critic_data),
                        "consecutive_critic_failures": state.consecutive_critic_failures,
                    },
                )
            except Exception as exc:
                logger.debug("supergoal critic merge failed: %s", exc)
                state.consecutive_critic_failures += 1

        # Track consecutive judge parse failures. Reset on any usable reply,
        # including API / transport errors (parse_failed=False) so a flaky
        # network doesn't trip the auto-pause meant for bad judge models.
        if parse_failed:
            state.consecutive_parse_failures += 1
        else:
            state.consecutive_parse_failures = 0




        # Track consecutive judge API/transport errors separately.
        # "judge error: ..." in the reason signals an API call failure.
        if reason and reason.startswith("judge error:"):
            state.consecutive_judge_api_failures += 1
        else:
            state.consecutive_judge_api_failures = 0

        gate_vetoed = False
        done_followup_gate_ids: List[str] = []
        if verdict == "done" and getattr(state, "mode", "goal") == "supergoal":
            _update_supergoal_gates(state)
            gate_ids_before_reconcile = _passed_gate_ids(state)

            # Cache the stall identity before reconcile so ancillary gate
            # auto-pass (G1/G3/G4) does not silently reset the stall
            # counter for the gate that is fundamentally still blocking.
            _stall_gate_id = state.last_failed_gate_id
            _stall_count = state.same_gate_stall_count
            _reconcile_done_evidence_gates(state, last_response, reason)
            if _passed_gate_ids(state) != gate_ids_before_reconcile:
                _reset_gate_stall(state)
            _update_supergoal_gates(state)
            first_gate = _first_blocking_failed_gate(state)
            followup_gates = _open_followup_gates(state)
            if first_gate is not None:
                gate_vetoed = True
                if state.last_failed_gate_id == first_gate.id:
                    state.same_gate_stall_count += 1

                elif _stall_gate_id and first_gate.id == _stall_gate_id:
                    # Ancillary gates passed during reconcile but the same
                    # blocking gate is still the first failure.  Restore the saved
                    # stall identity and advance the counter so the stall
                    # guard accurately tracks consecutive same-gate vetoes.
                    state.last_failed_gate_id = _stall_gate_id
                    state.same_gate_stall_count = _stall_count + 1
                else:
                    state.last_failed_gate_id = first_gate.id
                    state.same_gate_stall_count = 1
                verdict = "continue"
                reason = f"completion judge said done, but blocking supergoal gate {first_gate.id} remains open: {first_gate.description}"
                state.last_verdict = verdict
                state.last_reason = reason
                state.should_replan = True
                state.replan_count += 1
                if not state.next_best_action:
                    state.next_best_action = f"Satisfy gate {first_gate.id}: {first_gate.description}"
                _update_supergoal_plan_from_progress(state, verdict="continue")
                if state.same_gate_stall_count >= DEFAULT_MAX_SAME_GATE_STALLS:
                    state.status = "paused"
                    state.paused_reason = (
                        f"supergoal gate {first_gate.id} is stalled after "
                        f"{state.same_gate_stall_count} done-veto attempts: {first_gate.description}"
                    )
                    save_goal(self.session_id, state)
                    self._record_event(
                        "paused",
                        summary=state.paused_reason,
                        data={
                            "reason": state.paused_reason,
                            "trigger": "same_gate_stall",
                            "gate_id": first_gate.id,
                            "same_gate_stall_count": state.same_gate_stall_count,
                        },
                    )
                    return {
                        "status": "paused",
                        "should_continue": False,
                        "continuation_prompt": None,
                        "verdict": "continue",
                        "reason": reason,
                        "message": f"⏸ Supergoal paused — gate {first_gate.id} is stalled. {first_gate.description}",
                    }
            else:
                _reset_gate_stall(state)
                done_followup_gate_ids = [g.id for g in followup_gates]
                if followup_gates:
                    reason = f"{reason}; follow-up gates open: {', '.join(done_followup_gate_ids)}"

        if verdict == "done":
            state.status = "done"
            state.should_replan = False
            state.next_best_action = ""
            state.action_proposal = SupergoalActionProposal()
            state.hard_gate_reason = ""
            _reset_gate_stall(state)
            _update_supergoal_plan_from_progress(state, verdict="done")
            save_goal(self.session_id, state)
            self._record_event("done", summary=reason, data={"reason": reason})
            return {
                "status": "done",
                "control_status": "done_with_followups" if done_followup_gate_ids else "done",
                "followup_gate_ids": done_followup_gate_ids,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "done",
                "reason": reason,
                "message": f"✓ Goal achieved: {reason}",
            }

        # Auto-pause when the judge model can't produce the expected JSON
        # verdict N turns in a row. Points the user at the goal_judge config
        # so they can route this side task to a model that follows the
        # contract (e.g. google/gemini-3-flash-preview). Without this guard,
        # weak judge models burn the entire turn budget returning prose or
        # empty strings.
        if state.consecutive_parse_failures >= DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES:
            state.status = "paused"
            state.paused_reason = (
                f"judge model returned unparseable output {state.consecutive_parse_failures} turns in a row"
            )
            save_goal(self.session_id, state)
            self._record_event(
                "paused",
                summary=state.paused_reason,
                data={"reason": state.paused_reason, "trigger": "parse_failures"},
            )
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — the judge model ({state.consecutive_parse_failures} turns) "
                    "isn't returning the required JSON verdict. Route the judge to a stricter "
                    "model in ~/.hermes/config.yaml:\n"
                    "  auxiliary:\n"
                    "    goal_judge:\n"
                    "      provider: openrouter\n"
                    "      model: google/gemini-3-flash-preview\n"
                    "Then /goal resume to continue."
                ),
            }

        # Auto-pause when the judge API keeps failing (e.g. CPA exhausted,
        # endpoint down, auth errors).  Without this guard, a dead judge
        # endpoint causes the supergoal to loop forever with
        # verdict="continue" from the fallback in judge_goal().
        if state.consecutive_judge_api_failures >= DEFAULT_MAX_CONSECUTIVE_JUDGE_API_FAILURES:
            state.status = "paused"
            state.paused_reason = (
                f"judge API failed {state.consecutive_judge_api_failures} turns in a row "
                f"(last: {state.last_reason})"
            )
            save_goal(self.session_id, state)
            self._record_event(
                "paused",
                summary=state.paused_reason,
                data={"reason": state.paused_reason, "trigger": "judge_api_failures"},
            )
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — the judge API failed "
                    f"{state.consecutive_judge_api_failures} turns in a row. "
                    "Check CPA quota / endpoint health, then /goal resume."
                ),
            }

        # Supergoal board/critic is part of the control system, not a cosmetic
        # side-task. If it cannot update for several turns, fail closed only
        # when deterministic board reconciliation also has no useful evidence.
        # This keeps CPA-only deployments alive when the LLM critic times out
        # but the turn itself produced visible tool-backed progress.
        deterministic_board_progress = bool(
            state.evidence
            or state.research_findings
            or state.action_history
        )
        if (
            getattr(state, "mode", "goal") == "supergoal"
            and state.consecutive_critic_failures >= DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES
            and not deterministic_board_progress
        ):
            state.status = "paused"
            state.paused_reason = (
                f"supergoal critic/board update failed {state.consecutive_critic_failures} turns in a row"
            )
            state.should_replan = True
            state.next_best_action = state.next_best_action or "Repair auxiliary critic/board update path, then resume with a strategic replan."
            save_goal(self.session_id, state)
            self._record_event(
                "paused",
                summary=state.paused_reason,
                data={"reason": state.paused_reason, "trigger": "critic_failures"},
            )
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Supergoal paused — critic/board update failed "
                    f"{state.consecutive_critic_failures} turns in a row. Check auxiliary goal_judge/critic health, then /supergoal resume."
                ),
            }

        if state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = f"turn budget exhausted ({state.turns_used}/{state.max_turns})"
            save_goal(self.session_id, state)
            self._record_event(
                "paused",
                summary=state.paused_reason,
                data={"reason": state.paused_reason, "trigger": "budget"},
            )
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — {state.turns_used}/{state.max_turns} turns used. "
                    "Use /goal resume to keep going, or /goal clear to stop."
                ),
            }

        if not gate_vetoed:
            _reset_gate_stall(state)

        save_goal(self.session_id, state)
        self._record_event(
            "turn_evaluated",
            summary=reason,
            data={
                "verdict": verdict,
                "reason": reason,
                "turns_used": state.turns_used,
                "current_step_id": state.current_step_id,
            },
        )
        return {
            "status": "active",
            "should_continue": True,
            "continuation_prompt": self.next_continuation_prompt(),
            "verdict": "continue",
            "reason": reason,
            "message": (
                f"↻ Continuing toward {'supergoal' if getattr(state, 'mode', 'goal') == 'supergoal' else 'goal'} ({state.turns_used}/{state.max_turns}): {reason}"
            ),
        }

    def next_continuation_prompt(self) -> Optional[str]:
        if not self._state or self._state.status != "active":
            return None
        if getattr(self._state, "mode", "goal") == "supergoal":
            if self._state.subgoals:
                prompt = SUPERGOAL_CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
                    goal=self._state.goal,
                    subgoals_block=self._state.render_subgoals_block(),
                )
            else:
                prompt = SUPERGOAL_CONTINUATION_PROMPT_TEMPLATE.format(goal=self._state.goal)
            prompt += SUPERGOAL_BOARD_BLOCK_TEMPLATE.format(
                board=self._state.render_supergoal_board()
            )
            if self._state.hard_gate_reason:
                prompt += SUPERGOAL_HARD_GATE_BLOCK_TEMPLATE.format(reason=self._state.hard_gate_reason)
            if self._state.search_phase == "failure_taxonomy":
                taxonomy = "; ".join(
                    f"{k}={v}"
                    for k, v in sorted(
                        (self._state.failure_taxonomy or {}).items(),
                        key=lambda kv: (-kv[1], kv[0]),
                    )[:8]
                ) or "not yet classified"
                criteria = "; ".join(self._state.admission_criteria or []) or "independent source + baseline + kill criteria + OOS/rolling verifier required"
                prompt += SUPERGOAL_FAILURE_TAXONOMY_BLOCK_TEMPLATE.format(
                    taxonomy=taxonomy,
                    criteria=criteria,
                )
            if self._state.should_replan:
                prompt += SUPERGOAL_REPLAN_BLOCK
                self._record_event(
                    "replan_prompted",
                    summary=self._state.next_best_action or "replan requested",
                    data={"replan_count": self._state.replan_count},
                )
                # Consume the replan flag once it has been injected. If the
                # critic still sees drift/stuck behavior after the next turn it
                # will set the flag again.
                self._state.should_replan = False
                save_goal(self.session_id, self._state)
            return prompt
        if self._state.subgoals:
            return CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
                goal=self._state.goal,
                subgoals_block=self._state.render_subgoals_block(),
            )
        return CONTINUATION_PROMPT_TEMPLATE.format(goal=self._state.goal)


# ──────────────────────────────────────────────────────────────────────
# Kanban worker goal loop
# ──────────────────────────────────────────────────────────────────────

# Continuation prompt fed back to a kanban goal-mode worker that has not
# yet completed/blocked its task. The card's own acceptance criteria are
# the goal — the worker already has the full task body in its first turn,
# so we keep this short and point it back at the lifecycle contract.
KANBAN_GOAL_CONTINUATION_TEMPLATE = (
    "[Continuing toward this kanban task — judge says it is not done yet]\n"
    "Reason: {reason}\n\n"
    "Take the next concrete step toward completing the task. When the work "
    "is genuinely finished, call kanban_complete with a summary. If you are "
    "blocked and need human input, call kanban_block with a reason. Do not "
    "stop without calling one of them."
)

# Fed when the judge believes the work is done but the worker never called
# kanban_complete / kanban_block. One explicit nudge to terminate the task
# the right way before the loop gives up.
KANBAN_GOAL_FINALIZE_TEMPLATE = (
    "[The work looks complete, but the task is still open]\n"
    "Reason: {reason}\n\n"
    "If the task is genuinely done, call kanban_complete now with a short "
    "summary of what you did. If something still blocks completion, call "
    "kanban_block with the reason instead."
)


def run_kanban_goal_loop(
    *,
    task_id: str,
    goal_text: str,
    run_turn,
    task_status_fn,
    block_fn,
    max_turns: int = DEFAULT_MAX_TURNS,
    first_response: str = "",
    log=None,
) -> Dict[str, Any]:
    """Drive a kanban worker through a Ralph-style goal loop.

    The dispatcher spawns a goal-mode worker exactly like a normal worker
    (``hermes -p <profile> chat -q "work kanban task <id>"``). The worker's
    first turn has already run by the time this is called; ``first_response``
    is that turn's reply. From here we:

    1. Check whether the worker already terminated the task (called
       ``kanban_complete`` / ``kanban_block``). If so, stop — nothing to do.
    2. Otherwise judge the latest response against ``goal_text`` (the card's
       title + body). ``continue`` → feed a continuation prompt and run
       another turn IN THE SAME SESSION via ``run_turn``. ``done`` but the
       task is still open → one explicit "call kanban_complete" nudge.
    3. When the turn budget is exhausted and the worker still hasn't
       terminated the task, ``block_fn`` is invoked so the card lands in a
       sticky ``blocked`` state for human review (NOT a silent exit).

    This function performs NO SessionDB persistence — a worker process is
    ephemeral, so the turn budget lives in a local counter. It is fully
    decoupled from the CLI for testability: callers inject ``run_turn``
    (str -> str), ``task_status_fn`` (() -> str|None), and ``block_fn``
    (reason: str -> None).

    Returns a decision dict: ``{"outcome", "turns_used", "reason"}`` where
    outcome is one of ``"completed_by_worker"``, ``"blocked_budget"``,
    ``"blocked_by_worker"``, or ``"stopped"``.
    """

    def _log(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    max_turns = int(max_turns or DEFAULT_MAX_TURNS)
    if max_turns < 1:
        max_turns = DEFAULT_MAX_TURNS

    last_response = first_response or ""
    # The first turn already consumed one unit of budget.
    turns_used = 1
    nudged_to_finalize = False

    while True:
        # Did the worker terminate the task itself this turn?
        try:
            status = task_status_fn()
        except Exception as exc:
            _log(f"kanban goal loop: status check failed ({exc}); stopping")
            return {"outcome": "stopped", "turns_used": turns_used, "reason": "status check failed"}

        if status == "done":
            _log(f"kanban goal loop: task {task_id} completed by worker after {turns_used} turn(s)")
            return {"outcome": "completed_by_worker", "turns_used": turns_used, "reason": "worker completed the task"}
        if status == "blocked":
            _log(f"kanban goal loop: task {task_id} blocked by worker after {turns_used} turn(s)")
            return {"outcome": "blocked_by_worker", "turns_used": turns_used, "reason": "worker blocked the task"}
        if status not in ("running", "ready"):
            # Reclaimed / archived / unexpected — let the dispatcher own it.
            _log(f"kanban goal loop: task {task_id} status={status!r}; stopping")
            return {"outcome": "stopped", "turns_used": turns_used, "reason": f"status={status}"}

        # Still open — judge whether the latest response satisfies the card.
        verdict, reason, _parse_failed = judge_goal(goal_text, last_response)
        _log(f"kanban goal loop: turn {turns_used}/{max_turns} verdict={verdict} reason={_truncate(reason, 120)}")

        if verdict == "done":
            if nudged_to_finalize:
                # Already asked once to call kanban_complete and it still
                # didn't — block for review rather than spin.
                _log(f"kanban goal loop: task {task_id} judged done but worker won't finalize; blocking")
                try:
                    block_fn(
                        f"Goal-mode worker's output looked complete but it never "
                        f"called kanban_complete after a finalize nudge ({reason})."
                    )
                except Exception as exc:
                    _log(f"kanban goal loop: block_fn failed ({exc})")
                return {"outcome": "blocked_budget", "turns_used": turns_used, "reason": "judged done, never finalized"}
            prompt = KANBAN_GOAL_FINALIZE_TEMPLATE.format(reason=_truncate(reason, 400))
            nudged_to_finalize = True
        else:
            prompt = KANBAN_GOAL_CONTINUATION_TEMPLATE.format(reason=_truncate(reason, 400))

        # Budget check BEFORE spending another turn.
        if turns_used >= max_turns:
            _log(f"kanban goal loop: task {task_id} exhausted {turns_used}/{max_turns} turns; blocking")
            try:
                block_fn(
                    f"Goal-mode worker exhausted its turn budget "
                    f"({turns_used}/{max_turns}) without completing the task. "
                    f"Last judge verdict: {_truncate(reason, 300)}"
                )
            except Exception as exc:
                _log(f"kanban goal loop: block_fn failed ({exc})")
            return {"outcome": "blocked_budget", "turns_used": turns_used, "reason": "turn budget exhausted"}

        # Run another turn in the same session.
        try:
            last_response = run_turn(prompt) or ""
        except Exception as exc:
            _log(f"kanban goal loop: run_turn failed ({exc}); stopping")
            return {"outcome": "stopped", "turns_used": turns_used, "reason": f"run_turn error: {type(exc).__name__}"}
        turns_used += 1


__all__ = [
    "GoalState",
    "GoalManager",
    "CONTINUATION_PROMPT_TEMPLATE",
    "CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE",
    "SUPERGOAL_CONTINUATION_PROMPT_TEMPLATE",
    "SUPERGOAL_CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE",
    "JUDGE_USER_PROMPT_TEMPLATE",
    "JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE",
    "KANBAN_GOAL_CONTINUATION_TEMPLATE",
    "KANBAN_GOAL_FINALIZE_TEMPLATE",
    "DEFAULT_MAX_TURNS",
    "load_goal",
    "save_goal",
    "clear_goal",
    "judge_goal",
    "run_kanban_goal_loop",
]
