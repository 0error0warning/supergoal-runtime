from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from supergoal_runtime.domain import GoalEvent, GoalState
from supergoal_runtime.gates import update_supergoal_gates
from supergoal_runtime.projection import apply_events_to_state

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures"


def load_trace(name: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in (FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def new_state(goal: str, *, goal_run_id: str = "gr_replay") -> GoalState:
    state = GoalState(goal=goal, goal_run_id=goal_run_id, mode="supergoal")
    update_supergoal_gates(state)
    return state


def project_tool_ref(state: GoalState, ref: Any) -> bool:
    return apply_events_to_state(
        state,
        [
            GoalEvent(
                ts=0.0,
                type="tool_evidence_observed",
                turn=state.turns_used,
                summary=ref.claim,
                data={"evidence_ref": ref.to_dict()},
            )
        ],
        update_gates=update_supergoal_gates,
    )
