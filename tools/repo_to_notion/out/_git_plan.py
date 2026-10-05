"""只读：判定每个仓库是否属于 github.com/XiaoliZhouCN，以及各分支相对 develop 的合并状态。"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(r'D:\Repositories')
OUT = Path(r'D:\Repositories\Manager\AtriumPyTools\tools\repo_to_notion\out')
survey = json.loads((OUT / 'git_survey.json').read_text(encoding='utf-8'))

TARGET = re.compile(r'github(?:-shirley)?[:\/]+XiaoliZhouCN/', re.I)


def git(repo, *args):
    try:
        done = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=60)
        return (done.stdout or '').strip()
    except Exception as exc:  # noqa: BLE001
        return f'<ERROR {exc}>'


def is_ancestor(repo, ancestor, descendant):
    if not ancestor or not descendant:
        return None
    done = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', ancestor, descendant],
                          capture_output=True, text=True)
    return done.returncode == 0


def ref_exists(repo, ref):
    return bool(git(repo, 'rev-parse', '--verify', '--quiet', ref))


rows = []
for row in survey:
    repo = Path(row['repo'])
    origin = row['remotes'].get('origin', '')
    matches = bool(TARGET.search(origin))
    entry = {'repo': str(repo), 'origin': origin, 'matches': matches,
             'head': row['head'], 'dirty': row['dirty_count'],
             'origin_head': row['origin_head'],
             'other_remotes': {k: v for k, v in row['remotes'].items() if k != 'origin'}}
    if matches:
        base = 'develop' if ref_exists(repo, 'develop') else 'origin/develop'
        entry['develop_local'] = ref_exists(repo, 'develop')
        entry['develop_remote'] = ref_exists(repo, 'origin/develop')
        entry['base'] = base if ref_exists(repo, base) else None
        merged_local, unmerged_local = [], []
        for name in row['local_branches']:
            if name == 'develop':
                continue
            if not entry['base']:
                unmerged_local.append(name)
                continue
            (merged_local if is_ancestor(repo, name, entry['base']) else unmerged_local).append(name)
        merged_remote, unmerged_remote = [], []
        for name in row['remote_branches']:
            if not name.startswith('origin/') or name == 'origin/develop':
                continue
            if not entry['base']:
                unmerged_remote.append(name)
                continue
            (merged_remote if is_ancestor(repo, name, entry['base']) else unmerged_remote).append(name)
        entry['merged_local'] = merged_local
        entry['unmerged_local'] = unmerged_local
        entry['merged_remote'] = merged_remote
        entry['unmerged_remote'] = unmerged_remote
        entry['default_branch_remote'] = row['origin_head'].replace('origin/', '') if row['origin_head'] else ''
    rows.append(entry)

(OUT / 'git_plan.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')

print('匹配 XiaoliZhouCN 的仓库：')
for entry in rows:
    if not entry['matches']:
        continue
    print(f"\n{entry['repo']}")
    print(f"   origin={entry['origin']}")
    print(f"   HEAD={entry['head']}  未提交={entry['dirty']}  远端默认分支={entry['default_branch_remote'] or '(未设置)'}")
    print(f"   develop: 本地={entry['develop_local']} 远端={entry['develop_remote']} 基准={entry['base']}")
    print(f"   本地未合并到 develop: {entry['unmerged_local']}")
    print(f"   本地已合并(可直接删): {entry['merged_local']}")
    print(f"   远端未合并: {entry['unmerged_remote']}")
    print(f"   远端已合并(可直接删): {entry['merged_remote']}")

print('\n\n不匹配的仓库（应跳过）：')
for entry in rows:
    if entry['matches']:
        continue
    print(f"  {entry['repo']:<60} origin={entry['origin'] or '(无 remote)'}")
