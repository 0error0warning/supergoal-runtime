"""Finite SWE2 concurrency probe; small synthetic prompts, no benchmark scores."""

from concurrent.futures import ThreadPoolExecutor
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import statistics
import time
import urllib.error
import urllib.request


ROOT = Path("/var/lib/supergoal-lab")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="model-capacity-probe02")
    parser.add_argument("--levels", default="4,6")
    parser.add_argument("--waves", type=int, default=1)
    args = parser.parse_args()
    if not args.label.replace("-", "").isalnum() or not 1 <= args.waves <= 3:
        raise ValueError("Invalid finite probe parameters")
    levels = [int(n) for n in args.levels.split(",")]
    if not levels or any(n < 1 or n > 8 for n in levels):
        raise ValueError("Probe supports 1 to 8 concurrent requests")
    target = ROOT / "setup" / (args.label + ".json")
    if target.exists():
        raise FileExistsError(target)
    provider = json.loads((ROOT / "private/model.json").read_text())
    receipt = {"started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "model": "devin/swe-2", "status": "running", "levels": [],
               "synthetic_prompts_only": True, "max_output_tokens": 128,
               "overlapping_cohort": "public-gcp01", "separate_from_task_scores": True}

    def save():
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(receipt, indent=2))
        tmp.replace(target)

    def request(level, wave, index):
        marker = hashlib.sha256(f"supergoal-capacity01:{level}:{wave}:{index}".encode()).hexdigest()[:12]
        body = {"model": "devin/swe-2", "instructions": "Return the exact requested marker only.",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": marker}]}],
                "max_output_tokens": 128, "stream": True, "store": False}
        req = urllib.request.Request(provider["base_url"].rstrip("/") + "/responses",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json",
            "Authorization": "Bearer " + provider["api_key"]})
        start = time.monotonic()
        row = {"wave": wave, "index": index, "passed": False}
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                row["http_status"] = response.status
                text = ""
                for line in response:
                    if not line.startswith(b"data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                    except ValueError:
                        continue
                    if event.get("type") == "response.output_text.delta":
                        text += event.get("delta", "")
                    if event.get("type") == "response.completed":
                        result = event.get("response", {})
                        row.update(response_status=result.get("status"), response_model=result.get("model"),
                                   usage=result.get("usage"))
                        messages = [item for item in result.get("output", []) if item.get("type") == "message"]
                        final_text = "".join(c.get("text", "") for item in messages[-1:]
                                             for c in item.get("content", []))
                        row["completed_text"] = final_text[:2000]
                        row["streamed_text"] = text[:2000]
                        text = final_text or text
                row["expected_marker"] = marker
                row["passed"] = text.strip() == marker and row.get("response_status") == "completed"
                row["transport_passed"] = row["http_status"] == 200 and row.get("response_status") == "completed"
        except Exception as exc:
            row.update(error_type=type(exc).__name__, http_status=getattr(exc, "code", None))
        row["seconds"] = time.monotonic() - start
        return row

    save()
    try:
        for concurrency in levels:
            start = time.monotonic()
            rows = []
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                for wave in range(args.waves):
                    futures = [pool.submit(request, concurrency, wave, index) for index in range(concurrency)]
                    rows.extend(f.result() for f in futures)
            seconds = time.monotonic() - start
            times = sorted(row["seconds"] for row in rows)
            receipt["levels"].append({"concurrency": concurrency, "requests": rows,
                "all_passed": all(row["passed"] for row in rows), "wall_seconds": seconds,
                "transport_all_passed": all(row.get("transport_passed") for row in rows),
                "requests_per_minute": len(rows) / seconds * 60,
                "latency_median_seconds": statistics.median(times), "latency_max_seconds": max(times)})
            save()
            if not all(row.get("transport_passed") for row in rows):
                receipt["status"] = "stopped_on_failure"
                break
        else:
            receipt["status"] = "complete"
    finally:
        receipt["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()


if __name__ == "__main__":
    main()
