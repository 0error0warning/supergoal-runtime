"""Optional, model-independent guidance and evidence coverage for long goals.

An inferred brief is working memory, not permission, a replacement for the user
request, or a trusted executable checker. Coverage validation checks provenance
and observation references; semantic correctness still needs an independent
evaluation. No domain, benchmark name, or task-specific rule belongs here.
"""
from __future__ import annotations

import hashlib
import json
import re


POLICY_ID = "evidence-v1"
WORK_GUIDANCE = """Work toward the user's actual outcome. Keep the original request
authoritative, and distinguish stated requirements, contextual facts, and your
assumptions. First inspect relevant available context when it can resolve a gap.
Choose the next action by the information missing: do a clear, direct task
immediately; investigate uncertain local behavior; research primary sources and
implementations before committing to a method when the goal needs a technical
choice or current knowledge. Research should resolve a named decision: record
the evidence, alternatives, limitations, and the resulting implementation or
experiment. There is no source-count quota. Stop gathering when the evidence is
sufficient for the next reversible step, and revisit it when observations
contradict the approach. Ask only for consequential user-only information that
cannot be recovered from context; do independent work while waiting. Guesses
never grant permission or become user requirements.
Work in useful increments. A checkpoint records the changed artifact, observed
check result, remaining uncertainty, and next concrete action. Repetition or
tokens consumed are not progress. When an approach fails, use the failure to
change the next experiment rather than repeating the same claim. Before claiming
completion, test each requested behavior on the current artifact, including new
requirements and earlier behavior that could regress. Distinguish not checked
from failed. State the scope of evidence and unresolved gaps honestly."""


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _object(text, tag):
    matches = re.findall(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    if len(matches) != 1:
        raise ValueError(f"one {tag} object required")
    value = json.loads(matches[0])
    if not isinstance(value, dict):
        raise ValueError("object required")
    return value


def _text(value, *, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("nonempty bounded text required")
    return value.strip()


def _texts(value, *, limit=64):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("bounded text list required")
    return [_text(item) for item in value]


def discovery_prompt(instruction):
    return WORK_GUIDANCE + "\n" + """Prepare a compact working brief, without doing
the implementation. You have a snapshot for read-only inspection; use tools only
where context is needed. The brief is advisory. Do not turn optional enhancements
into requirements or answer unknown private preferences yourself. Split actual
requested outcomes into independently checkable requirements, preserving important
qualifiers. For each stated requirement include an EXACT quote from the original
request. Put contextual inferences and defaults in assumptions instead. If research
is needed, name the decisions it must resolve, not a generic reading list.
Return exactly one <goal_brief> JSON object with this schema:
{"route":"direct|inspect|research", "route_reason":"...",
 "requirements":[{"id":"R1","requirement":"...","source_quote":"...",
                  "verification":"observable way to check this requirement"}],
 "assumptions":["assumption and how to validate it"],
 "information_gaps":["missing fact, how to obtain it, and consequence"],
 "research_questions":["decision that external evidence must resolve"],
 "next_action":"concrete next step"}</goal_brief>.
Do not omit the remaining request just to fit a checklist.
ORIGINAL REQUEST (authoritative):\n""" + instruction


def parse_brief(text, instruction):
    """Reject invented provenance; preserve the entire request in every brief."""
    data = _object(text, "goal_brief")
    if data.get("route") not in {"direct", "inspect", "research"}:
        raise ValueError("unknown work route")
    requirements = data.get("requirements")
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 64:
        raise ValueError("requirements required")
    clean, ids = [], set()
    for row in requirements:
        if not isinstance(row, dict):
            raise ValueError("requirement object required")
        name = _text(row.get("id"), limit=64)
        quote = _text(row.get("source_quote"))
        if name in ids or quote not in instruction:
            raise ValueError("duplicate identifier or unsupported source quote")
        ids.add(name)
        clean.append({"id": name, "requirement": _text(row.get("requirement")),
                      "source_quote": quote, "verification": _text(row.get("verification"))})
    return {"schema": 1, "policy": POLICY_ID, "authority": "advisory_model_interpretation",
            "original_request": instruction,
            "request_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
            "route": data["route"], "route_reason": _text(data.get("route_reason")),
            "requirements": clean, "assumptions": _texts(data.get("assumptions")),
            "information_gaps": _texts(data.get("information_gaps")),
            "research_questions": _texts(data.get("research_questions")),
            "next_action": _text(data.get("next_action"))}


def fallback_brief(instruction, reason):
    """A failed interpreter must not silently discard or rewrite the task."""
    return {"schema": 1, "policy": POLICY_ID, "authority": "uninterpreted_request",
            "original_request": instruction,
            "request_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
            "route": "inspect", "route_reason": "Brief unavailable: " + str(reason),
            "requirements": [{"id": "original", "requirement": instruction,
                              "source_quote": instruction, "verification": "Check all original clauses"}],
            "assumptions": [], "information_gaps": [], "research_questions": [],
            "next_action": "Inspect the request and relevant workspace, then act."}


def execution_prompt(prompt, brief):
    return (WORK_GUIDANCE + "\nCurrent controller context:\n" + prompt
            + "\nAdvisory working brief; the original request has priority:\n" + _encode(brief))


def review_prompt(brief, answer):
    return """Independently audit completion on this snapshot. The executor's claim
and the inferred brief are fallible. Check the ENTIRE original request for omitted
clauses before using the checklist. Do not invent optional goals. Read-only source
inspection can support structural claims, but behavior claims require exercising
the behavior; run checks in temporary writable scratch space if necessary. Do not
repair deliverables or fetch benchmark solutions. Check new and existing behavior
separately where relevant. Passing a coarse test suite is not evidence that every
new requirement works. Sources must support the specific claims, not merely exist.
For each requirement report pass, fail, or unknown and explain the actual check.
Evidence must include an EXACT, distinctive quote from a tool observation made
during THIS audit (not from the executor claim). Use at least 8 characters per
quote; print a descriptive result when a tool otherwise only returns exit status.
Do not reuse observations from another artifact. Unobserved behavior is unknown.
Return exactly one <coverage> JSON object:
{"request_covered":true, "omissions":[], "requirements":[
 {"id":"R1", "verdict":"pass|fail|unknown", "reason":"specific observation",
  "evidence":[{"quote":"exact tool output", "supports":"specific assertion"}]}],
 "next_action":"targeted repair/check, or none if complete"}</coverage>.
request_covered means all original clauses were considered, including any missing
from the inferred list; omissions lists original clauses not established by this
audit. An unobservable criterion must remain unknown. A completion claim alone is
not an observation.\n""" + _encode({"brief": brief, "untrusted_executor_claim": answer})


def _tool_text(messages):
    # Match both the raw SDK content and parsed string fields in JSON tool output.
    def strings(value):
        if isinstance(value, str):
            yield value
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                return
            if not isinstance(decoded, str):
                yield from strings(decoded)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
    return [text for message in messages if message.get("role") == "tool"
            for text in strings(message.get("content"))]


def assess_coverage(text, brief, messages, *, artifact_id):
    """Fail closed for missing/stale/ungrounded coverage, without claiming truth."""
    scope = "Model semantic audit with structurally checked evidence references; not independent task truth"
    try:
        _text(artifact_id)
        data = _object(text, "coverage")
        if type(data.get("request_covered")) is not bool:
            raise ValueError("explicit request coverage required")
        omissions = _texts(data.get("omissions"))
        action = _text(data.get("next_action"))
        rows = data.get("requirements")
        expected = {r["id"] for r in brief["requirements"]}
        if not isinstance(rows, list) or len(rows) != len(expected):
            raise ValueError("one result for every requirement required")
        observations, seen, clean = _tool_text(messages), set(), []
        for row in rows:
            if not isinstance(row, dict) or row.get("id") not in expected or row["id"] in seen:
                raise ValueError("unknown or duplicate requirement")
            seen.add(row["id"])
            if row.get("verdict") not in {"pass", "fail", "unknown"}:
                raise ValueError("strict requirement verdict required")
            reason = _text(row.get("reason"))
            evidence = row.get("evidence")
            if not isinstance(evidence, list) or len(evidence) > 32:
                raise ValueError("bounded observation list required")
            refs = []
            for observation in evidence:
                quote = _text(observation.get("quote"), limit=4000)
                support = _text(observation.get("supports"))
                if len(quote) < 8 or not any(quote in output for output in observations):
                    raise ValueError("evidence quote not found in this audit's tool observations")
                refs.append({"quote": quote, "supports": support,
                             "artifact_id": artifact_id,
                             "quote_sha256": hashlib.sha256(quote.encode()).hexdigest()})
            if row["verdict"] in {"pass", "fail"} and not refs:
                raise ValueError("decisive verdict requires observed evidence")
            clean.append({"id": row["id"], "verdict": row["verdict"], "reason": reason, "evidence": refs})
        if omissions or not data["request_covered"]:
            verdict = "fail"
        elif any(row["verdict"] == "fail" for row in clean):
            verdict = "fail"
        elif any(row["verdict"] == "unknown" for row in clean):
            verdict = "unknown"
        else:
            verdict = "pass"
        return {"verdict": verdict, "reason": action, "requirements": clean,
                "omissions": omissions, "request_covered": data["request_covered"],
                "request_sha256": brief["request_sha256"], "artifact_id": artifact_id, "scope": scope}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"verdict": "unknown", "reason": "Coverage not established: " + str(exc),
                "artifact_id": artifact_id, "scope": scope}
