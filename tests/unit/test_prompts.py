from __future__ import annotations

from supergoal_runtime.prompts import build_judge_messages


def test_judge_prompt_includes_persisted_state_board() -> None:
    messages = build_judge_messages(
        "finish two phases",
        "phase 2 complete",
        state_board="turns_used: 2/20; latest_completed_turn: 2\ngates: G1=passed G3=passed G4=passed\nevidence: phase1.txt, phase2.txt, result.json",
        turn_number=2,
    )

    assert len(messages) == 2
    user = messages[1]["content"]
    assert "Persisted state board" in user
    assert "G4=passed" in user
    assert "result.json" in user
    assert "completed turn 2" in user
    assert "phase 2 complete" in user
