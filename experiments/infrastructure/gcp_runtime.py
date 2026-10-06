"""Prepare the versioned experiment runtime on the dedicated GCP host."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile


ROOT = Path("/var/lib/supergoal-lab")
COMMIT = "7131e4375048a0e408a8fb404b5f499d726b695b"


def main():
    receipt = ROOT / "setup/gcp-runtime.json"
    if receipt.exists():
        raise FileExistsError(receipt)
    state = {"started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "status": "running", "commands": [], "model_calls": 0}

    def save():
        temp = receipt.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2))
        temp.replace(receipt)

    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(ROOT / "python"),
               UV_CACHE_DIR=str(ROOT / "uv-cache"), PYTHONDONTWRITEBYTECODE="1")

    def run(args, **kwargs):
        state["phase"] = " ".join(map(str, args))
        save()
        subprocess.run(list(map(str, args)), check=True, env=env, timeout=1800, **kwargs)
        state["commands"].append(state["phase"])
        save()

    save()
    try:
        archive = ROOT / "setup/hermes-core-private.tar.xz"
        runtime = ROOT / "private/runtime"
        source = runtime / "hermes-v0.21.3-251bedc-mt2"
        if source.exists():
            raise FileExistsError(source)
        runtime.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                path = Path(member.name)
                if (path.is_absolute() or ".." in path.parts
                        or path.parts[0] != source.name
                        or not (member.isfile() or member.isdir())):
                    raise ValueError(f"Unsafe source archive member: {member.name}")
            tar.extractall(runtime, filter="data")
        state["hermes_source_archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        run(["python3", "-m", "venv", ROOT / "bootstrap-venv"])
        run([ROOT / "bootstrap-venv/bin/pip", "install", "uv"])
        uv = ROOT / "bootstrap-venv/bin/uv"
        run([uv, "python", "install", "3.13.5"])
        for name in ["hermes", "harbor"]:
            target = ROOT / f"{name}-venv"
            run([uv, "venv", "--python", "3.13.5", target])
            args = [uv, "pip", "install", "--python", target / "bin/python"]
            if name == "hermes":
                args += ["--require-hashes"]
            run(args + ["-r", ROOT / f"setup/{name}-requirements.txt"])
            result = subprocess.run([target / "bin/python", "-c",
                "import importlib.metadata,json,sys; print(json.dumps({'python':sys.version,"
                "'packages':sorted(d.metadata['Name']+'=='+d.version for d in importlib.metadata.distributions())}))"],
                check=True, capture_output=True, text=True)
            state[name] = json.loads(result.stdout)
        upstream = ROOT / "upstream/terminal-bench-2-1"
        upstream.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", "--filter=blob:none",
             "https://github.com/harbor-framework/terminal-bench-2-1.git", upstream])
        run(["git", "-C", upstream, "checkout", "--detach", COMMIT])
        state["upstream_commit"] = subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
        if state["upstream_commit"] != COMMIT:
            raise ValueError("Upstream commit mismatch")
        for name in ["control", "jobs", "exports"]:
            (ROOT / name).mkdir(mode=0o700, exist_ok=True)
        state["status"] = "complete"
    except BaseException as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        state["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()


if __name__ == "__main__":
    main()
