"""Evidence ledger primitives for /supergoal.

The controller must not treat assistant prose as tool-backed provenance.  This
module records observed tool results as durable goal events and gives projections
a typed trust boundary: assistant claims are hints, while tool/file/test/web
observations can satisfy evidence gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional


TrustLevel = Literal["claim", "observed", "verified"]


class EvidenceSource(str, Enum):
    TOOL_CALL = "tool_call"
    FILE_ARTIFACT = "file_artifact"
    TEST_RUN = "test_run"
    WEB_SOURCE = "web_source"
    GITHUB_SOURCE = "github_source"
    HUMAN_INPUT = "human_input"
    ASSISTANT_CLAIM = "assistant_claim"


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    goal_run_id: str
    turn_id: str
    source: EvidenceSource
    tool_call_id: Optional[str]
    artifact_path: Optional[str]
    locator: Optional[str]
    content_hash: Optional[str]
    observed_at: str
    claim: str
    validates: list[str] = field(default_factory=list)
    trust_level: TrustLevel = "observed"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        return data

    @classmethod
    def from_dict(cls, data: Any) -> Optional["EvidenceRef"]:
        if not isinstance(data, dict):
            return None
        eid = str(data.get("id") or "").strip()
        claim = str(data.get("claim") or "").strip()
        if not eid or not claim:
            return None
        try:
            source = EvidenceSource(str(data.get("source") or EvidenceSource.ASSISTANT_CLAIM.value))
        except Exception:
            source = EvidenceSource.ASSISTANT_CLAIM
        trust = str(data.get("trust_level") or "claim").strip().lower()
        if trust not in {"claim", "observed", "verified"}:
            trust = "claim"
        validates = data.get("validates")
        return cls(
            id=eid,
            goal_run_id=str(data.get("goal_run_id") or ""),
            turn_id=str(data.get("turn_id") or ""),
            source=source,
            tool_call_id=str(data.get("tool_call_id") or "") or None,
            artifact_path=str(data.get("artifact_path") or "") or None,
            locator=str(data.get("locator") or "") or None,
            content_hash=str(data.get("content_hash") or "") or None,
            observed_at=str(data.get("observed_at") or ""),
            claim=claim[:500],
            validates=[str(x) for x in validates[:12]] if isinstance(validates, list) else [],
            trust_level=trust,  # type: ignore[arg-type]
        )


def _text_result(result: Any, *, limit: int = 2000) -> str:
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, sort_keys=True)
        except Exception:
            text = str(result)
    text = " ".join(text.split())
    return text[:limit]


def _hash_text(text: str) -> str:
    if not text:
        return ""
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:24]


_PATH_RE = re.compile(r"(?:(?:^|\s)(?:[./~][\w./-]+|[\w.-]+/[\w./-]+)\.(?:py|ts|tsx|js|json|md|txt|csv|log|html|yaml|yml|png|jpg|pdf))", re.I)
_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.I)


def _first_locator(args: dict[str, Any], result_text: str) -> tuple[str, str]:
    for key in ("url", "urls", "query", "path", "file_path", "target", "command"):
        value = args.get(key)
        if isinstance(value, list) and value:
            return str(value[0])[:240], ""
        if value:
            s = str(value)
            path_match = _PATH_RE.search(s)
            return s[:240], path_match.group(0).strip() if path_match else ""
    url = _URL_RE.search(result_text)
    if url:
        return url.group(0)[:240], ""
    path = _PATH_RE.search(result_text)
    if path:
        p = path.group(0).strip()
        return p[:240], p[:240]
    return "", ""


def classify_tool_evidence(tool_name: str, args: dict[str, Any], result: Any, *, status: str = "") -> tuple[EvidenceSource, TrustLevel, list[str]]:
    name = (tool_name or "").lower()
    result_text = _text_result(result, limit=1200).lower()
    ok = (status or "ok").lower() not in {"error", "blocked", "cancelled"}
    trust: TrustLevel = "observed" if ok else "claim"
    validates: list[str] = []
    if name in {"web_search", "web_extract", "browser_navigate", "browser_snapshot", "browser_get_images", "web"}:
        source = EvidenceSource.GITHUB_SOURCE if "github.com" in result_text or "github.com" in str(args).lower() else EvidenceSource.WEB_SOURCE
        validates.append("G2")
    elif name in {"read_file", "write_file", "patch", "search_files"}:
        source = EvidenceSource.FILE_ARTIFACT
        validates.append("G3")
        if name in {"write_file", "patch"} and ok:
            trust = "verified"
    elif name in {"terminal", "execute_code"}:
        cmd = str(args.get("command") or args.get("code") or "").lower()
        if any(k in cmd or k in result_text for k in ("pytest", "unittest", "npm test", "pnpm test", "cargo test", "go test", "passed", "failed")):
            source = EvidenceSource.TEST_RUN
            validates.extend(["G3", "G4"])
            trust = "verified" if ok else "observed"
        else:
            source = EvidenceSource.TOOL_CALL
            validates.append("G3")
    else:
        source = EvidenceSource.TOOL_CALL
    return source, trust, validates


def evidence_ref_from_tool_call(
    *,
    goal_run_id: str,
    turn_id: str,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    tool_call_id: str,
    status: str = "",
) -> Optional[EvidenceRef]:
    if not goal_run_id or not tool_call_id:
        return None
    if not isinstance(args, dict):
        args = {}
    result_text = _text_result(result)
    if not result_text:
        return None
    source, trust, validates = classify_tool_evidence(tool_name, args, result, status=status)
    locator, artifact_path = _first_locator(args, result_text)
    observed_at = datetime.now(timezone.utc).isoformat()
    basis = "|".join([goal_run_id, turn_id, tool_call_id, tool_name, locator, _hash_text(result_text)])
    eid = "ev_" + hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()[:16]
    claim = f"{tool_name} observed {locator or result_text[:160]}"
    return EvidenceRef(
        id=eid,
        goal_run_id=goal_run_id,
        turn_id=turn_id,
        source=source,
        tool_call_id=tool_call_id,
        artifact_path=artifact_path or None,
        locator=locator or None,
        content_hash=_hash_text(result_text),
        observed_at=observed_at,
        claim=claim[:500],
        validates=validates,
        trust_level=trust,
    )


def record_tool_evidence(
    *,
    session_id: str,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    tool_call_id: str,
    turn_id: str = "",
    status: str = "",
) -> Optional[EvidenceRef]:
    """Write a post-tool result into the bound supergoal event ledger.

    Fail-open by design: tool execution must never depend on supergoal state.
    """
    if not session_id or not tool_call_id:
        return None
    try:
        from hermes_cli.goals import append_goal_event, load_goal

        state = load_goal(session_id)
        if state is None or getattr(state, "mode", "goal") != "supergoal":
            return None
        goal_run_id = getattr(state, "goal_run_id", "") or session_id
        ref = evidence_ref_from_tool_call(
            goal_run_id=goal_run_id,
            turn_id=turn_id,
            tool_name=tool_name,
            args=args,
            result=result,
            tool_call_id=tool_call_id,
            status=status,
        )
        if ref is None:
            return None
        append_goal_event(
            session_id,
            "tool_evidence_observed",
            turn=int(getattr(state, "turns_used", 0) or 0),
            summary=ref.claim,
            data={"evidence_ref": ref.to_dict()},
            max_events=300,
        )
        return ref
    except Exception:
        return None


def research_finding_from_evidence(ref: EvidenceRef) -> Optional[dict[str, Any]]:
    if ref.source not in {EvidenceSource.WEB_SOURCE, EvidenceSource.GITHUB_SOURCE}:
        return None
    if ref.trust_level not in {"observed", "verified"} or not ref.tool_call_id:
        return None
    locator = (ref.locator or ref.artifact_path or ref.id or "").lower()
    if ref.source == EvidenceSource.GITHUB_SOURCE or "github.com" in locator:
        source_type = "github"
    elif "arxiv" in locator or "paper" in locator:
        source_type = "paper"
    elif "docs" in locator or "readthedocs" in locator:
        source_type = "docs"
    else:
        source_type = "web"
    return {
        "source_type": source_type,
        "title": ref.claim[:160],
        "locator": ref.locator or ref.artifact_path or ref.id,
        "claim": ref.claim[:240],
        "retrieved_at": ref.observed_at,
        "tool_call_id": ref.tool_call_id,
        "evidence_quote_or_hash": ref.content_hash or ref.id,
        "evidence_source": ref.source.value,
        "trust_level": ref.trust_level,
    }
