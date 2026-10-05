"""盘点各仓库里「没有进 git」的文件：未跟踪（??）与被忽略（!!）。"""
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(r'D:\Repositories')
OWNER = 'XiaoliZhouCN'
ORIGIN_RE = re.compile(rf'github(?:-shirley)?[:\/]+{OWNER}/', re.I)
SKIP_DIRS = {'node_modules', 'Library', 'Temp', 'obj', 'bin', 'dist', 'build', '.next',
             '__pycache__', '.venv', 'venv', 'target', '.dsh-build', 'vendor', 'ThirdParty',
             '.vs', 'Packages', 'snapshots', 'Logs', 'UserSettings'}


def sh(*args, cwd=None):
    d = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return ((d.stdout or '') + (d.stderr or '')).strip()


def human(n):
    return f'{n / 1048576:.1f} MB' if n >= 1048576 else f'{n / 1024:.0f} KB'


repos = sorted({p.parent for p in ROOT.rglob('.git')
                if p.is_dir() and not any(part in SKIP_DIRS for part in p.parts)},
               key=lambda p: str(p).lower())

grand_untracked = grand_ignored = 0
for repo in repos:
    url = sh('remote', 'get-url', 'origin', cwd=repo)
    if not ORIGIN_RE.search(url):
        continue
    out = sh('status', '--porcelain', '--ignored', '--untracked-files=all', cwd=repo)
    untracked, ignored = [], []
    for line in out.splitlines():
        if line.startswith('?? '):
            untracked.append(line[3:].strip().strip('"'))
        elif line.startswith('!! '):
            ignored.append(line[3:].strip().strip('"'))
    u_size = sum((repo / p).stat().st_size for p in untracked if (repo / p).is_file())
    grand_untracked += u_size
    print(f'\n=== {repo.name} ===')
    if untracked:
        print(f'  未跟踪 {len(untracked)} 个文件，共 {human(u_size)}')
        for p in untracked:
            size = (repo / p).stat().st_size if (repo / p).is_file() else 0
            print(f'    {human(size):>10}  {p}')
    else:
        print('  未跟踪：无')
    if ignored:
        groups = defaultdict(lambda: [0, 0])
        for p in ignored:
            top = p.split('/')[0] if '/' in p else p
            f = repo / p
            groups[top][0] += 1
            groups[top][1] += f.stat().st_size if f.is_file() else 0
        total = sum(v[1] for v in groups.values())
        grand_ignored += total
        print(f'  被忽略 {len(ignored)} 个文件，共 {human(total)}（前 8 组）')
        for top, (count, size) in sorted(groups.items(), key=lambda kv: -kv[1][1])[:8]:
            print(f'    {human(size):>10}  {count:>5} 个  {top}')

print(f'\n合计：未跟踪 {human(grand_untracked)}，被忽略 {human(grand_ignored)}')
