"""Pure prompt templates and builders for Supergoal.

This module contains no model client code. Runtime adapters inject LLM
callbacks and pass these strings to the host-owned provider.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

JUDGE_RESPONSE_SNIPPET_CHARS = 4000

CONTINUATION_PROMPT_TEMPLATE = (
    "[Continuing toward your standing supergoal]\n"
    "Goal: {goal}\n\n"
    "{board_block}"
    "Continue working toward this goal. Take the next concrete step. "
    "If you believe the goal is complete, state so explicitly and stop. "
    "If you are blocked and need input from the user, say so clearly and stop."
)

CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "[Continuing toward your standing supergoal]\n"
    "Goal: {goal}\n\n"
    "Additional criteria the user added mid-loop:\n"
    "{subgoals_block}\n\n"
    "{board_block}"
    "Continue working toward the goal AND all additional criteria. Take "
    "the next concrete step. If you believe the goal and every additional "
    "criterion are complete, state so explicitly and stop. If you are blocked "
    "and need input from the user, say so clearly and stop."
)

JUDGE_SYSTEM_PROMPT = (
    "You are a strict judge evaluating whether an autonomous agent has achieved "
    "a user's stated goal. Reply only with JSON."
)

JUDGE_USER_PROMPT_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Is the goal satisfied? Reply as "
    '{{"done": <true|false>, "reason": "<one-sentence rationale>"}}'
)

JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Additional criteria the user added mid-loop (all must also be satisfied "
    "for the goal to be DONE):\n{subgoals_block}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Decision: For each numbered criterion above, require concrete evidence. "
    "If any criterion lacks evidence, return done=false. Reply as JSON."
)

SUPERGOAL_CRITIC_SYSTEM_PROMPT = (
    "You are a strategic critic for a long-running autonomous agent. Do not "
    "decide final completion; update compact working memory and identify drift, "
    "repetition, weak progress, missing evidence, and the need to replan. "
    "Reply only with one JSON object."
)

SUPERGOAL_CRITIC_USER_PROMPT_TEMPLATE = (
    "Supergoal:\n{goal}\n\n"
    "Existing state board:\n{board}\n\n"
    "Most recent agent response:\n{response}\n\n"
    "Return JSON with keys such as inferred_user_intent, success_definition, "
    "first_principles_model, existing_solution_scan, research_findings, "
    "hypothesis_portfolio, action_proposal, no_edge_report, "
    "build_vs_reuse_decision, literalism_risk, research_sufficiency, progress, "
    "strategy_health, root_cause_confidence, should_replan, next_best_action, "
    "missing_evidence, new_milestones, new_hypotheses, new_evidence, "
    "new_attempted_solutions, new_blockers, and new_risks."
)


def truncate(text: Any, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit].rstrip() + "... [truncated]"


def render_subgoals_block(subgoals: Sequence[str] | None) -> str:
    return "\n".join(
        f"- {i}. {text}" for i, text in enumerate([s for s in (subgoals or []) if str(s).strip()], start=1)
    )


def build_continuation_prompt(state: Any, *, board: str = "") -> str | None:
    if state is None or getattr(state, "status", "") != "active":
        return None
    board_block = f"Current state board:\n{board}\n\n" if board else ""
    subgoals = list(getattr(state, "subgoals", []) or [])
    if subgoals:
        return CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal=getattr(state, "goal", ""),
            subgoals_block=render_subgoals_block(subgoals),
            board_block=board_block,
        )
    return CONTINUATION_PROMPT_TEMPLATE.format(
        goal=getattr(state, "goal", ""),
        board_block=board_block,
    )


def build_judge_messages(
    goal: str,
    last_response: str,
    *,
    subgoals: Sequence[str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    current_time = (now or datetime.now(timezone.utc)).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    clean_subgoals = [str(s).strip() for s in (subgoals or []) if str(s).strip()]
    if clean_subgoals:
        user = JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal=truncate(goal, 2000),
            subgoals_block=truncate(render_subgoals_block(clean_subgoals), 2000),
            response=truncate(last_response, JUDGE_RESPONSE_SNIPPET_CHARS),
            current_time=current_time,
        )
    else:
        user = JUDGE_USER_PROMPT_TEMPLATE.format(
            goal=truncate(goal, 2000),
            response=truncate(last_response, JUDGE_RESPONSE_SNIPPET_CHARS),
            current_time=current_time,
        )
    return [{"role": "system", "content": JUDGE_SYSTEM_PROMPT}, {"role": "user", "content": user}]


def build_critic_messages(
    state: Any,
    last_response: str,
    *,
    goal_chars: int = 1200,
    board_chars: int = 2400,
    response_chars: int = 2400,
) -> list[dict[str, str]]:
    board = state.render_supergoal_board() if hasattr(state, "render_supergoal_board") else ""
    user = SUPERGOAL_CRITIC_USER_PROMPT_TEMPLATE.format(
        goal=truncate(getattr(state, "goal", ""), goal_chars),
        board=truncate(board, board_chars),
        response=truncate(last_response, response_chars),
    )
    return [{"role": "system", "content": SUPERGOAL_CRITIC_SYSTEM_PROMPT}, {"role": "user", "content": user}]
