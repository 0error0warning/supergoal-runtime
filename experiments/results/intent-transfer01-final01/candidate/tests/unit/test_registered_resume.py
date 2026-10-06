"""A reviewed interruption must never turn into an implicit model retry."""

from copy import deepcopy

import pytest

from experiments.public_benchmarks.await_registered_resume import validate_resume


def records():
    return (
        {"candidate": "public-dev04", "disposition": "retain_failure_continue_unstarted",
         "control_id": "failed-trial", "next_registered_index": 16},
        {"status": "stopped_for_audit", "rows": [
            {"index": 15, "agent_metadata": {"control_id": "failed-trial"}}]},
        {"experiment": "public-dev04", "planned_order": [{}] * 30},
    )


def test_reviewed_failure_allows_only_the_remaining_interval():
    audit, previous, registration = records()
    validate_resume(audit, previous, registration, 16, 30)
    for start in (15, 17):
        with pytest.raises(ValueError, match="immediately following"):
            validate_resume(audit, previous, registration, start, 30)


def test_active_batch_or_unrelated_audit_cannot_authorize_a_resume():
    audit, previous, registration = records()
    active = deepcopy(previous)
    active["status"] = "running"
    with pytest.raises(ValueError, match="must have stopped"):
        validate_resume(audit, active, registration, 16, 30)
    audit["control_id"] = "another-trial"
    with pytest.raises(ValueError, match="does not describe"):
        validate_resume(audit, previous, registration, 16, 30)


def test_unreviewed_failure_cannot_be_skipped():
    audit, previous, registration = records()
    audit["disposition"] = "needs_audit"
    with pytest.raises(ValueError, match="closed failure audit"):
        validate_resume(audit, previous, registration, 16, 30)
