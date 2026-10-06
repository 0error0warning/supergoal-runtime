"""Versioned, procedural development tasks and reserved evaluation fixtures.

The returned `private` section stays in the supervisor and is never mounted in
the executor. These are controlled tasks, not a representative industry sample.
"""
from __future__ import annotations

import csv
import io
import random
from datetime import datetime

FAMILIES = ("code", "data", "synthesis")
ARMS = ("hermes", "native_goal", "sg_v1", "sg_v2", "sg_v2_no_context", "sg_v2_no_verification")


def reference(events, cutoff):
    end = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    latest = {}
    for index, event in enumerate(events):
        instant = datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00"))
        if instant > end:
            continue
        key = (int(event["revision"]), instant, index)
        if event["id"] not in latest or key > latest[event["id"]][0]:
            latest[event["id"]] = (key, event)
    return [{"id": name, "value": int(latest[name][1]["value"])}
            for name in sorted(latest) if latest[name][1]["op"] == "upsert"]


SPEC = """Select events whose observed_at is at or before cutoff, comparing timezone-aware instants.
For each id select the highest INTEGER revision among the eligible events, then the latest observed_at
instant, then the later input position to break exact ties. If that selected event has op=delete,
omit the id; otherwise output its integer value (zero and negative values are valid).
Return a list of objects with exactly id and value, sorted lexicographically by id. Never mutate inputs.
All timestamps have an explicit UTC offset or Z; ids are case-sensitive Unicode strings;
revisions and values may be strings or integers. Empty inputs produce an empty list."""


def cases(seed, count=14):
    rng = random.Random(seed)
    result = [{"events": [], "cutoff": "2026-06-01T12:00:00Z"}]
    for n in range(count):
        events = []
        for name in (f"device-{n}", "装置甲", "A", "a"):
            for rev in (1, 2, 10):
                events.append({"id": name, "revision": str(rev), "value": str(rng.randint(-30, 50)),
                               "op": rng.choice(["upsert", "upsert", "delete"]),
                               "observed_at": rng.choice(["2026-06-01T09:00:00Z", "2026-06-01T20:00:00+08:00", "2026-06-01T07:00:00-05:00", "2026-06-01T12:00:01Z"])})
        events += [{"id": "zero", "revision": 1, "value": 0, "op": "upsert", "observed_at": "2026-06-01T12:00:00Z"}]
        rng.shuffle(events)
        events += [dict(events[0], value="41"), dict(events[0], value="42")]
        result.append({"events": events, "cutoff": "2026-06-01T12:00:00Z"})
    return result


def build(family, seed):
    rng = random.Random(seed)
    public_case = {"events": [
        {"id": "sensor", "revision": "1", "op": "upsert", "value": "7", "observed_at": "2026-06-01T10:00:00Z"},
        {"id": "sensor", "revision": "2", "op": "upsert", "value": "0", "observed_at": "2026-06-01T11:00:00Z"}],
        "cutoff": "2026-06-01T12:00:00Z"}
    if family == "code":
        files = {"normalizer.py": "def normalize(events, cutoff):\n    latest = {}\n    for e in events:\n        if e['observed_at'] < cutoff and e['value']:\n            latest[e['id']] = e\n    return [{'id': k, 'value': int(v['value'])} for k,v in latest.items()]\n",
                 "SPEC.md": SPEC}
        prompt = "Repair normalizer.py so normalize(events, cutoff) implements SPEC.md for arbitrary valid inputs. Keep the function signature; use Python standard library only. Add your own regression tests."
        check = "import sys,copy\nsys.path.insert(0,'/workspace')\nfrom normalizer import normalize\nc=" + repr(public_case) + "\nbefore=copy.deepcopy(c)\nassert normalize(**c)==[{'id':'sensor','value':0}]\nassert c==before\nassert normalize([], c['cutoff'])==[]\nprint('public examples passed')\n"
        private = {"cases": cases(seed), "expected": [reference(**x) for x in cases(seed)]}
        artifacts = ["normalizer.py"]
    elif family == "data":
        events = [event for case in cases(seed, 7)[1:] for event in case["events"]]
        paths, files = [], {}
        for i in range(3):
            path = f"inputs/part-{i}.csv"
            paths.append(path)
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=["id", "revision", "value", "op", "observed_at"])
            writer.writeheader()
            writer.writerows(events[i::3])
            files[path] = out.getvalue()
        ordered = [e for i in range(3) for e in events[i::3]]
        files["SPEC.md"] = SPEC + "\nInput order is part-0.csv, part-1.csv, part-2.csv, preserving row order within each file.\nCutoff: 2026-06-01T12:00:00Z."
        prompt = "Reconcile all three input CSV files under inputs/ using SPEC.md. Deliver result.json with exactly the required list, and a self-contained standard-library Python script reproduce.py which regenerates it. Validate edge cases, not just the row count."
        check = "import json\nfrom pathlib import Path\nx=json.loads(Path('result.json').read_text())\nassert isinstance(x,list)\nassert all(set(r)=={'id','value'} and type(r['value']) is int for r in x)\nassert [r['id'] for r in x]==sorted(set(r['id'] for r in x))\nassert {'id':'zero','value':0} in x\nassert Path('reproduce.py').is_file()\nprint('public schema and anchor passed')\n"
        private = {"expected": reference(ordered, "2026-06-01T12:00:00Z")}
        artifacts = ["result.json", "reproduce.py"]
    elif family == "synthesis":
        files, expected = {}, []
        for index in range(12):
            name = f"device-{index:02}"
            selected = rng.choice([2, 10])
            tier = rng.choice(["standard", "restricted", "priority"])
            days = rng.choice([0, 7, 14, 30])
            approved = f"sources/{name}-v{selected}.md"
            files[approved] = f"id: {name}\nrevision: {selected}\nstatus: approved\neffective: 2026-06-01\ntier: {tier}\nretention_days: {days}\nowner: team-{index%3}\n"
            files[f"sources/{name}-v1.md"] = f"id: {name}\nrevision: 1\nstatus: approved\neffective: 2025-01-01\ntier: obsolete\nretention_days: 90\nowner: old-team\n"
            files[f"sources/{name}-v20-draft.md"] = f"id: {name}\nrevision: 20\nstatus: draft\neffective: 2026-05-01\ntier: future\nretention_days: 120\nowner: draft-team\n"
            files[f"sources/{name}-v30-future.md"] = f"id: {name}\nrevision: 30\nstatus: approved\neffective: 2026-07-01\ntier: future\nretention_days: 365\nowner: future-team\n"
            expected.append({"id": name, "tier": tier, "retention_days": days, "owner": f"team-{index%3}", "source": approved})
        files["DECISION_RULES.md"] = "As of 2026-06-15, choose each device's highest INTEGER revision with status=approved and effective date <= as-of date. Drafts and future-effective entries are not authoritative. Report every device. Cite the selected source's exact workspace-relative path."
        prompt = "Synthesize the conflicting local policy sources following DECISION_RULES.md. Deliver decisions.json sorted by id; each object must have exactly id, tier, retention_days (integer), owner, source. Also write BRIEF.md summarizing which conflicts were resolved, with source citations and no invented facts."
        check = "import json\nfrom pathlib import Path\nx=json.loads(Path('decisions.json').read_text())\nassert len(x)==12\nassert all(set(r)=={'id','tier','retention_days','owner','source'} and type(r['retention_days']) is int and Path(r['source']).is_file() for r in x)\nassert [r['id'] for r in x]==sorted(set(r['id'] for r in x))\nassert len(Path('BRIEF.md').read_text())>=100\nprint('public coverage and citation shape passed')\n"
        private = {"expected": expected}
        artifacts = ["decisions.json", "BRIEF.md"]
    else:
        raise ValueError(family)
    prompt += "\nThe public acceptance command is: python /task/accept.py. Provided inputs, source documents and specifications are read-only. The full written requirements remain authoritative; passing examples alone may be insufficient. Work only under /workspace; finish when the deliverables are correct."
    return {"id": f"{family}-{seed}", "family": family, "seed": seed, "files": files,
            "prompt": prompt, "accept": check, "private": private,
            "contract": {"outcome": prompt, "artifacts": artifacts,
                         "checks": [{"id": "public-acceptance", "argv": ["/opt/python/bin/python3.11", "/task/accept.py"]}]}}
