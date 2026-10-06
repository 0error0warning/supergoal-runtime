# Reproduce the analysis

These scripts are supplemental to the frozen live runner. They cannot be used
as newly preregistered evidence for the original matrix. Input and analysis
hashes accompany the recorded outputs.

Run from the repository root after the final result files have been collected:

```powershell
.\.venv\Scripts\python.exe experiments/analysis/registered_results.py --batches experiments/results/holdout01a-results.json experiments/results/holdout01b-results.json
.\.venv\Scripts\python.exe -m pytest experiments/analysis/test_registered_results.py -q
```

The analyzer checks exact registration coverage, duplicate attempts, fixture
hashes, original randomization order, requested/reported model aliases, call
ceilings and outcome-field consistency. It refuses to call a partial study
complete. `--allow-partial` produces an explicitly incomplete progress report.

The `false_completion` input field is displayed as **unmet task at terminal
stop**. It is not a blinded semantic judgment of an agent's final message.
Per-family fixtures are related, repetitions are not new tasks, and the table
does not turn equal observed performance into a statistical equivalence claim.

Optional figure generation uses standard Matplotlib and only completed recorded
data. Its dependencies do not enter the runtime's package requirements:

```powershell
uv pip install --python .venv/Scripts/python.exe -r experiments/analysis/requirements-plot.txt
.\.venv\Scripts\python.exe experiments/analysis/plot_results.py
```

The resulting PNG, SVG and PDF preserve the distinction between elapsed soak
duration and short active SDK episodes. Non-episode time includes waiting,
queuing, startup and checks. No illustrative or projected data are substituted.

Fault probes are separately described in `protocol-process-crash.md` and the
script docstrings. The live context-policy supplement has its own
`protocol-context-reset.md` and registration. Run the server-only scripts through
the documented isolated laboratory setup, not against a production Hermes home.
