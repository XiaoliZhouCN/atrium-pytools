"""只读扫描工作区里所有 git 仓库的远端 / 分支 / 脏工作区状态，输出 JSON + 可读报告。"""
import json
import subprocess
from pathlib import Path

ROOT = Path(r'D:\Repositories')
OUT = Path(r'D:\Repositories\Manager\AtriumPyTools\tools\repo_to_notion\out')
SKIP = {'node_modules', 'Library', 'Temp', 'obj', 'bin', 'dist', 'build', '.next',
        '__pycache__', '.venv', 'venv', 'target', '.dsh-build', 'vendor', 'ThirdParty',
        '.vs', 'Packages', 'snapshots', 'Logs', 'UserSettings'}


def git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=60)
        return (done.stdout or '').strip()
    except Exception as exc:  # noqa: BLE001
        return f'<ERROR {exc}>'


def find_repos() -> list[Path]:
    repos = []
    for path in ROOT.rglob('.git'):
        if not path.is_dir():
            continue
        if any(part in SKIP for part in path.parts):
            continue
        repos.append(path.parent)
    return sorted(repos, key=lambda p: str(p).lower())


rows = []
for repo in find_repos():
    remotes = {}
    for line in git(repo, 'remote', '-v').splitlines():
        parts = line.split()
        if len(parts) >= 2:
            remotes.setdefault(parts[0], parts[1])
    head = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD')
    dirty = [l for l in git(repo, 'status', '--porcelain').splitlines() if l.strip()]
    branches = {}
    for line in git(repo, 'for-each-ref', '--format=%(refname:short)%09%(upstream:short)%09%(upstream:track)',
                    'refs/heads').splitlines():
        bits = line.split('\t')
        branches[bits[0]] = {'upstream': bits[1] if len(bits) > 1 else '',
                             'track': bits[2] if len(bits) > 2 else ''}
    remote_branches = [l for l in git(repo, 'for-each-ref', '--format=%(refname:short)',
                                      'refs/remotes').splitlines()
                       if l and not l.endswith('/HEAD')]
    ab = {}
    for ref in ('origin/main', 'origin/develop', 'origin/master'):
        if git(repo, 'rev-parse', '--verify', '--quiet', ref):
            cnt = git(repo, 'rev-list', '--left-right', '--count', f'HEAD...{ref}')
            ab[ref] = cnt
    default = git(repo, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD').replace('refs/remotes/', '')
    rows.append({
        'repo': str(repo), 'remotes': remotes, 'head': head, 'dirty_count': len(dirty),
        'dirty': dirty[:20], 'local_branches': branches, 'remote_branches': remote_branches,
        'ahead_behind': ab, 'origin_head': default,
        'last_commit': git(repo, 'log', '-1', '--format=%h %ad %s', '--date=short'),
    })

(OUT / 'git_survey.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')

lines = []
for row in rows:
    lines.append('=' * 90)
    lines.append(f"{row['repo']}")
    for name, url in row['remotes'].items():
        lines.append(f"  remote {name}: {url}")
    lines.append(f"  HEAD: {row['head']}   origin/HEAD: {row['origin_head'] or '(未设置)'}")
    lines.append(f"  last: {row['last_commit']}")
    lines.append(f"  工作区: {row['dirty_count']} 个未提交条目")
    for item in row['dirty']:
        lines.append(f"      {item}")
    if row['dirty_count'] > len(row['dirty']):
        lines.append(f"      ...(共 {row['dirty_count']})")
    lines.append(f"  本地分支 ({len(row['local_branches'])}):")
    for name, info in row['local_branches'].items():
        lines.append(f"      {name}  upstream={info['upstream'] or '-'}  track={info['track'] or ''}")
    lines.append(f"  远端分支 ({len(row['remote_branches'])}): {', '.join(row['remote_branches']) or '-'}")
    for ref, cnt in row['ahead_behind'].items():
        left, right = (cnt.split() + ['?', '?'])[:2]
        lines.append(f"  HEAD vs {ref}: 领先 {left} / 落后 {right}")
(OUT / 'git_survey.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(f'repos: {len(rows)}')
print(f'report: {OUT / "git_survey.txt"}')
