"""核对各仓库收敛后的分支情况（本地 + 远端）。"""
import re
import subprocess
from pathlib import Path

OWNER = 'XiaoliZhouCN'
ORIGIN_RE = re.compile(rf'github(?:-shirley)?[:\/]+{OWNER}/', re.I)
ROOT = Path(r'D:\Repositories')
SKIP_DIRS = {'node_modules', 'Library', 'Temp', 'obj', 'bin', 'dist', 'build', '.next',
             '__pycache__', '.venv', 'venv', 'target', '.dsh-build', 'vendor', 'ThirdParty',
             '.vs', 'Packages', 'snapshots', 'Logs', 'UserSettings'}


def sh(cmd, cwd=None):
    d = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return (d.stdout or '') + (d.stderr or '')


repos = sorted({p.parent for p in ROOT.rglob('.git')
                if p.is_dir() and not any(part in SKIP_DIRS for part in p.parts)},
               key=lambda p: str(p).lower())

print(f'{"仓库":<26} {"本地分支":<34} {"远端分支(origin)"}')
print('-' * 110)
for repo in repos:
    url = sh(['git', '-C', str(repo), 'remote', 'get-url', 'origin']).strip()
    if not ORIGIN_RE.search(url):
        continue
    local = sh(['git', '-C', str(repo), 'for-each-ref', '--format=%(refname:short)',
                'refs/heads']).split()
    remote_raw = sh(['git', '-C', str(repo), 'ls-remote', '--heads', 'origin'])
    remote = []
    for line in remote_raw.splitlines():
        if 'refs/heads/' in line:
            remote.append(line.split('refs/heads/')[1].strip())
    flag = '✅' if sorted(local) == ['develop', 'main'] and sorted(remote) == ['develop', 'main'] else '⚠'
    print(f'{flag} {repo.name:<24} {str(local):<34} {str(sorted(remote))}')
