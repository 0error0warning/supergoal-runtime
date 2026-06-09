# Supergoal Principles

`/supergoal` is an experiment in turning a chat agent into a long-running mission runtime.

The purpose is not simply to raise the turn budget. The purpose is to make the agent capable of spending a very large budget responsibly: preserving state, measuring progress, verifying artifacts, avoiding drift, and stopping or branching when evidence says the current path is exhausted.

## The honest critique

A naive high-budget mode is just automatic token burning.

If the loop is:

```text
agent acts → judge says continue → agent acts again
```

then more budget can make the system worse. It can create more logs, more files, more validators, more audits, and more confident summaries without producing the thing the user actually needed.

`/supergoal` starts from this critique. Its roadmap is to replace “long prompt + continuation” with a control system.

## Target capability

The target `/supergoal` mode should be able to:

1. Run for a very long time with a very high token budget.
2. Maintain durable mission state across turns, compression, and resumes.
3. Infer and preserve root intent, not merely literal instructions.
4. Track gates, evidence, hypotheses, artifacts, risks, and costs.
5. Choose actions by expected information gain, risk, cost, and user priority.
6. Verify outputs with tools, tests, logs, files, citations, and ledgers.
7. Detect inertia and out-of-scope behavior.
8. Branch, stop, ask, or write no-edge/blocked reports when appropriate.
9. Convert failed traces into tests and runtime improvements.
10. Preserve normal `/goal` as a simple, low-overhead mode.

## Non-negotiable principles

### First principles are the starting point

Every long run must repeatedly reconstruct the mission from first principles:

- root intent, not just literal wording;
- causal model, not just checklist;
- constraints and tradeoffs, not just requested actions;
- failure modes, not just success stories;
- falsification evidence, not just confirming evidence.

If the agent cannot explain why the current step follows from the mission's load-bearing truths, it should not spend more budget on that step.

### Critical spirit is required

`/supergoal` should be skeptical of easy answers, including its own. It must ask:

- What am I assuming?
- What would prove this path wrong?
- Am I optimizing a subtask while missing the mission?
- Is this artifact evidence, or just activity?
- Am I continuing because it is useful, or because inertia is easier than stopping?

The agent should not be rude or contrarian, but it should be intellectually adversarial toward weak plans and unverified assumptions.

### The shortest reliable path wins

A supergoal should prefer the shortest path that can satisfy the mission with evidence.

This means:

- reuse before build;
- diagnose before refactor;
- one verified artifact before a platform;
- one decisive experiment before a broad framework;
- stop or write no-edge when evidence says the path is exhausted.

Long runtime does not justify long detours. The high budget exists to allow deeper verification and iteration, not to excuse unnecessary complexity.

### Mission state is authoritative

Conversation history is not enough. Mission-critical facts must be externalized into structured state and artifacts.

### Every gate needs a verifier

A gate that cannot be verified is just a suggestion. Verifiers can be deterministic checks, file existence, JSONPath assertions, test outputs, citations, ledger entries, or strict artifact readers.

### The critic is not the runtime

The critic can diagnose, but it should not be the source of truth. The runtime should compute gates from evidence where possible.

### Research must have provenance

A model saying “I found a paper” is not enough. Research findings need locator, query, retrieval time, tool call ID, quote/hash, relevance, and contradiction tracking.

### Hypotheses must be executable

A hypothesis without baseline, experiment, kill criteria, artifact, and verdict is not ready to drive a long-running loop.

### Replan must change behavior

If replan does not prevent the same action class from repeating, it is not a replan. It is just a more expensive continuation prompt.

### No-edge is a valid outcome

For research and trading tasks, “no edge found under tested assumptions” is a successful convergence mode if backed by evidence.

### Safety is part of intelligence

The more autonomy and tool access an agent has, the more it needs permission boundaries, negative constraints, scope checks, and destructive-action controls.

### Normal mode must remain normal

A powerful `/supergoal` should not make everyday `/goal` slower, noisier, or harder to predict.
