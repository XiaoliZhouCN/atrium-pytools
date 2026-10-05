"""诊断上一次 apply 的 5 个失败点（只读）。"""
import json
import subprocess
from pathlib import Path

OUT = Path(r'D:\Repositories\Manager\AtriumPyTools\tools\git_develop_sync\out')
report = json.loads((OUT / 'git_consolidate_report.json').read_text(encoding='utf-8'))

print('=== 完整失败信息 ===')
for item in report['failures']:
    print(f"\n--- {item['repo']} @ {item['stage']}")
    print(item['error'][:1500])

print('\n\n=== 各仓库当前状态 ===')


def sh(cmd, cwd=None):
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    return (done.stdout or '') + (done.stderr or '')


repos = [r['repo'] for r in report['results']]
for repo in repos:
    head = sh(['git', '-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD']).strip()
    locals_ = sh(['git', '-C', repo, 'for-each-ref', '--format=%(refname:short)', 'refs/heads']).split()
    status = sh(['git', '-C', repo, 'status', '--porcelain'])
    dirty = len([l for l in status.splitlines() if l.strip()])
    conflict = 'UU' in status or 'CONFLICT' in status
    print(f"{Path(repo).name:<26} HEAD={head:<28} 分支={locals_}  未提交={dirty}{'  ⚠冲突中' if conflict else ''}")

print('\n\n=== ChestNut_Village 远端是否存在 ===')
print(sh(['gh', 'repo', 'view', 'XiaoliZhouCN/ChestNut_Village', '--json', 'name,defaultBranchRef'],
         cwd=r'D:\Repositories\Projects\ChestNut_Village') or '(空)')

print('\n=== NexusRenderer 冲突文件 ===')
rn = r'D:\Repositories\Projects\NexusRenderer'
print(sh(['git', '-C', rn, 'status', '--porcelain'])[:600])
p = Path(rn) / '.trae/rules/git-commit-message.md'
if p.exists():
    text = p.read_text(encoding='utf-8', errors='replace')
    print(text[:1200])

print('\n=== shirleyzh-dsh-sessions 的 ws/ 分支 ===')
ss = r'D:\Repositories\DeepseekHarness\shirleyzh-dsh-sessions'
print(sh(['git', '-C', ss, 'for-each-ref', '--format=%(refname:short) %(objectname:short) %(committerdate:short) %(subject)',
          'refs/remotes/origin']))
