"""Fresh contexts with/without replay, starting from the same pinned artifact."""
import json
from pathlib import Path
import shlex
import uuid

from harbor.agents.oracle import OracleAgent
from harbor.models.trial.paths import TrialPaths

from context_multistep_agent import ContextMultiStepStudy
from environment_binding import bind_task_container
from grader_history import restore
from harbor_agent import HermesSupergoalStudy, ROOT, dump
from prefix_inputs import WORKSPACE_PROBE, load_prefix


async def setup_prefix(environment, docker, spec):
    container, info, binding = await bind_task_container(environment, docker)
    if info['Image'] != spec['image_id']:
        raise ValueError('Task did not start from the registered common image')
    probe = await environment.exec('python3 -c ' + shlex.quote(WORKSPACE_PROBE), user='root', timeout_sec=30)
    if probe.return_code:
        raise RuntimeError('Cannot measure initial workspace')
    measured = json.loads(probe.stdout)
    if measured['workspace_sha256'] != spec['workspace_sha256']:
        raise ValueError('Initial workspace differs from the common prefix')
    history = await restore(environment, spec['grader_history']['path'])
    return {'status': 'ready', 'container': container, 'binding': binding,
            'image_id': info['Image'], **measured, 'grader_history_restoration': history}


class PrefixReplayStudy(ContextMultiStepStudy):
    def __init__(self, *args, prefix_path, prefix_sha256, arm='fresh_ledger', **kwargs):
        if arm not in {'fresh_ledger', 'fresh_current'}:
            raise ValueError('Only the two registered fresh-context conditions are allowed')
        self.prefix_spec, public_requirement = load_prefix(prefix_path, prefix_sha256)
        self.prefix_ready = False
        super().__init__(*args, arm=arm, **kwargs)
        self.instructions = [public_requirement]
        self.docker_host = 'unix://' + str(ROOT / 'run/docker.sock')
        self.ledger.update(prefix_checkpoint_count=1, prefix_spec_sha256=prefix_sha256,
                           prefix_source=self.prefix_spec['origin'],
                           prefix_requests_observed=self.prefix_spec['prefix_requests'],
                           prefix_requests_charged_to_suffix=0,
                           public_requirements=list(self.instructions),
                           scope='Paired continuation from one frozen prefix; new physical requests counted only for suffix')
        dump(self.control / 'problem.json', self.ledger)

    def version(self):
        return 'context-prefix01'

    async def docker(self, *args, **kwargs):
        return await HermesSupergoalStudy.docker(self, *args, **kwargs)

    async def setup(self, environment):
        if self.prefix_ready:
            return
        # This is grader-owned history, restored before the first suffix run.
        # ContextMultiStepStudy conceals it before either model role begins and
        # restores the original bytes before each official verifier.
        self.ledger['prefix_setup'] = await setup_prefix(environment, self.docker, self.prefix_spec)
        self.prefix_ready = True
        dump(self.control / 'problem.json', self.ledger)

    async def run(self, instruction, environment, context):
        if not self.prefix_ready:
            raise RuntimeError('Common prefix setup did not complete')
        return await super().run(instruction, environment, context)


class PrefixOracleAgent(OracleAgent):
    """Original Harbor oracle with identical prefix setup and zero model calls."""
    def __init__(self, *, logs_dir, task_dir, prefix_path, prefix_sha256, **kwargs):
        self.prefix_spec, _ = load_prefix(prefix_path, prefix_sha256)
        self.prefix_sha256 = prefix_sha256
        self.docker_host = 'unix://' + str(ROOT / 'run/docker.sock')
        # Harbor 0.24 passes oracle-only arguments only when name == "oracle".
        # Import-path agents receive logs_dir; its parent is the trial root.
        logs_dir = Path(logs_dir)
        trial_paths = TrialPaths(trial_dir=logs_dir.parent)
        if trial_paths.agent_dir != logs_dir:
            raise ValueError('Unexpected Harbor agent log directory')
        super().__init__(logs_dir=logs_dir, task_dir=Path(task_dir),
                         trial_paths=trial_paths, **kwargs)

    async def docker(self, *args, **kwargs):
        return await HermesSupergoalStudy.docker(self, *args, **kwargs)

    async def setup(self, environment):
        measured = await setup_prefix(environment, self.docker, self.prefix_spec)
        dump(ROOT / 'setup' / ('prefix-reference-setup-' + uuid.uuid4().hex + '.json'),
             {'prefix_spec_sha256': self.prefix_sha256, 'model_calls': 0, **measured})
        return await super().setup(environment)
