import json

import pytest

from supergoal_runtime.v2.kernel import Kernel
from supergoal_runtime.v2.work_policy import assess_coverage, fallback_brief, parse_brief


def brief_text(**updates):
    data = {"route": "direct", "route_reason": "fully specified", "requirements": [
        {"id": "R1", "requirement": "Create output", "source_quote": "Write ready",
         "verification": "Read the current output"}], "assumptions": [], "information_gaps": [],
        "research_questions": [], "next_action": "Write the output"}
    data.update(updates)
    return "<goal_brief>" + json.dumps(data) + "</goal_brief>"


def coverage(**updates):
    data = {"request_covered": True, "omissions": [], "requirements": [
        {"id": "original", "verdict": "pass", "reason": "Observed output", "evidence": [
            {"quote": "verified output: ready", "supports": "expected content"}]}], "next_action": "none"}
    data.update(updates)
    return "<coverage>" + json.dumps(data) + "</coverage>"


def assess(text, messages=None):
    if messages is None:
        messages = [{"role": "tool", "content": json.dumps({"output": "verified output: ready"})}]
    return assess_coverage(text, fallback_brief("Write ready", "test"), messages, artifact_id="sha256:current")


def test_inference_preserves_request_and_does_not_become_authority():
    original = "Write ready; do not overwrite existing files."
    brief = parse_brief(brief_text(assumptions=["Use result.txt if no output name is specified"]), original)
    assert brief["original_request"] == original
    assert brief["authority"] == "advisory_model_interpretation"
    assert len(brief["requirements"]) == 1
    assert len(brief["assumptions"]) == 1


def test_invented_provenance_and_duplicate_objects_rejected():
    with pytest.raises(ValueError):
        parse_brief(brief_text(), "Only inspect files")
    with pytest.raises(ValueError):
        parse_brief(brief_text() + brief_text(), "Write ready")


def test_evidence_is_bound_to_audit_observation_and_artifact():
    result = assess(coverage())
    assert result["verdict"] == "pass"
    assert result["requirements"][0]["evidence"][0]["artifact_id"] == "sha256:current"
    assert assess(coverage(), [{"role": "assistant", "content": "verified output: ready"}])["verdict"] == "unknown"
    assert assess(coverage(), [{"role": "tool", "content": "old artifact only"}])["verdict"] == "unknown"


@pytest.mark.parametrize("change", [
    {"requirements": []}, {"request_covered": "true"},
    {"requirements": [{"id": "invented", "verdict": "pass"}]},
    {"requirements": [{"id": "original", "verdict": "pass", "reason": "sure", "evidence": []}]},
])
def test_incomplete_or_unsupported_pass_is_unknown(change):
    assert assess(coverage(**change))["verdict"] == "unknown"


def test_omitted_original_clause_prevents_completion_even_if_list_passes():
    assert assess(coverage(omissions=["Existing files were overwritten"]))["verdict"] == "fail"


def test_unknown_remains_unknown_even_with_other_observations():
    rows = [{"id": "original", "verdict": "unknown", "reason": "Cannot run behavior",
             "unknown_kind": "unobserved", "evidence": []}]
    assert assess(coverage(requirements=rows))["verdict"] == "unknown"
    assert assess(coverage(requirements=rows))["retryable"] is True


def test_missing_observation_surface_does_not_request_useless_reexecution(tmp_path):
    rows = [{"id": "original", "verdict": "unknown", "reason": "Snapshot lacks process state",
             "unknown_kind": "environment", "evidence": []}]
    result = assess(coverage(requirements=rows))
    assert result['verdict'] == 'unknown' and result['retryable'] is False
    kernel = Kernel(tmp_path / 'goals.db')
    kernel.create('g', {'outcome': 'Do work', 'verification_unknown': 'continue'})
    assert kernel.finish(kernel.claim('g', 'host'), result)['status'] == 'verification_unavailable'


def test_unknown_can_continue_without_becoming_success_or_resetting_budget(tmp_path):
    kernel = Kernel(tmp_path / "goals.db")
    kernel.create("g", {"outcome": "Do work", "work_policy": "evidence-v1", "verification_unknown": "continue"}, max_turns=2)
    lease = kernel.claim("g", "host")
    first = kernel.finish(lease, {"verdict": "unknown", "reason": "Observe remaining behavior"})
    assert first["status"] == "active"
    assert kernel.finish(lease, {"verdict": "unknown", "reason": "Observe remaining behavior"}) == first
    assert kernel.finish(kernel.claim("g", "host"), {"verdict": "unknown", "reason": "Still unknown"})["status"] == "budget_exhausted"
    assert kernel.state("g")["last_outcome"]["verdict"] == "unknown"


def test_existing_unknown_policy_still_stops(tmp_path):
    kernel = Kernel(tmp_path / "goals.db")
    kernel.create("g", {"outcome": "Do work"})
    result = kernel.finish(kernel.claim("g", "host"), {"verdict": "unknown", "reason": "No observation"})
    assert result["status"] == "verification_unavailable"
