"""Draw the completed recorded study; never substitute illustrative outcomes."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT/'experiments/results'


def main():
    data = json.loads((RESULTS/'holdout01-analysis.json').read_text(encoding='utf-8'))
    soak = json.loads((RESULTS/'soak-v2-r3-20261005-results.json').read_text(encoding='utf-8'))
    if not data['complete'] or data['audit_errors']:
        raise ValueError('A final figure requires complete, audited registered results')
    names = {'hermes': 'Hermes', 'native_goal': 'Native GoalManager',
             'sg_v1': 'Pre-study v1 candidate', 'sg_v2': 'Full v2',
             'sg_v2_no_context': 'V2 without state reminder',
             'sg_v2_no_verification': 'V2 without acceptance'}
    arms = list(names)
    colors = ['#16736b' if arm == 'sg_v2' else '#8495a7' for arm in arms]
    plt.rcParams.update({'font.size': 10, 'font.family': 'DejaVu Sans',
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.labelcolor': '#243746', 'text.color': '#243746',
                         'xtick.color': '#465766', 'ytick.color': '#243746',
                         'svg.fonttype': 'none', 'pdf.fonttype': 42})
    fig = plt.figure(figsize=(12.3, 7.7), facecolor='white')
    grid = fig.add_gridspec(2, 2, height_ratios=[2.1, 1], wspace=0.52, hspace=0.63)
    a, b, c = fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1]), fig.add_subplot(grid[1, :])
    y = range(len(arms))
    passes = [data['arms'][arm]['artifact_pass'] for arm in arms]
    trials = [data['arms'][arm]['n'] for arm in arms]
    a.barh(y, passes, color=colors, height=0.58)
    a.set_yticks(list(y), [names[x] for x in arms])
    a.invert_yaxis()
    a.set_xlim(0, max(trials)*1.25)
    a.set_xticks([0, 6, 12, 18])
    a.set_xlabel('Runs passing hidden outcome checks')
    a.set_title('A  Controlled task outcomes', loc='left', fontweight='bold', pad=17)
    for pos, passed, n in zip(y, passes, trials):
        a.text(passed+0.35, pos, f'{passed}/{n}', va='center', fontsize=10)
    requests = [data['arms'][arm]['physical_requests'] for arm in arms]
    b.barh(y, requests, color=colors, height=0.58)
    b.set_yticks(list(y), [names[x] for x in arms])
    b.invert_yaxis()
    b.set_xlim(0, max(requests)*1.21)
    b.set_xlabel('Upstream model requests, including judges and retries')
    b.set_title('B  Measured model usage', loc='left', fontweight='bold', pad=17)
    for pos, calls in zip(y, requests):
        b.text(calls+max(requests)*0.02, pos, str(calls), va='center', fontsize=10)
    elapsed = soak['elapsed_seconds']/60
    c.broken_barh([(0, elapsed)], (0.35, 0.3), facecolors='#dce3e8')
    intervals = []
    for e in soak['episodes']:
        end = e['worker_instance']/1e9-soak['started']
        intervals.append(((end-e['seconds'])/60, e['seconds']/60))
    c.broken_barh(intervals, (0.3, 0.4), facecolors='#16736b')
    for instance in soak['supervisor_instances'][1:]:
        moment = (instance['started']-soak['started'])/60
        c.axvline(moment, ymin=0.1, ymax=0.85, color='#b36320', linestyle='--', linewidth=1.3)
    c.plot(elapsed, 0.5, marker='o', color='#16736b' if soak['passed'] else '#ba3848', markersize=5)
    c.set_ylim(0.1, 1.1)
    c.set_yticks([])
    c.set_xlim(-0.5, elapsed+2)
    c.set_xticks([0, 30, 60, 90, 120])
    c.set_xlabel('Elapsed minutes after the staged-input experiment started')
    c.set_title('C  Actual elapsed duration and SDK activity', loc='left', fontweight='bold', pad=18)
    c.text(0, 0.99, f'Elapsed {elapsed:.1f} min  |  Active SDK episodes {soak["active_episode_seconds"]:.1f} s'
                    f'  |  Final registered check: {"pass" if soak["passed"] else "FAIL"}', fontsize=10)
    c.legend(handles=[Patch(facecolor='#16736b', label='SDK work'),
                      Patch(facecolor='#dce3e8', label='Waiting / queue / setup / checks'),
                      plt.Line2D([0], [0], color='#b36320', linestyle='--', label='Supervisor restart')],
             loc='lower center', bbox_to_anchor=(0.5, -0.78), ncol=3, frameon=False)
    fig.suptitle('Supergoal / Hermes / SWE2 — recorded pilot', x=0.055, y=0.97,
                 ha='left', fontsize=17, fontweight='bold')
    fig.text(0.055, 0.925, 'Nine procedural fixtures, two repeats per arm. Descriptive evidence; no general reliability claim.', fontsize=10)
    fig.subplots_adjust(left=0.22, right=0.97, top=0.85, bottom=0.2)
    fig.text(0.055, 0.035, 'The two-hour test contains three short execution episodes and staged waiting; it is not two hours of continuous reasoning.', fontsize=9)
    output = RESULTS/'figures'
    output.mkdir(exist_ok=True)
    for suffix in ('png', 'svg', 'pdf'):
        fig.savefig(output/f'pilot-results.{suffix}', dpi=190, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(output/'pilot-results.png')


if __name__ == '__main__':
    main()
