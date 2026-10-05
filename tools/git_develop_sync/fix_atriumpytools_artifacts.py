#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_atriumpytools_artifacts.py — 收拾 AtriumPyTools 里误入库的生成物。

背景：consolidate_develop.py 的 COMMIT_EXCLUDE key 写成了 `atrium-pytools`，而代码按目录名
`AtriumPyTools` 查表，导致排除从未生效，下面这些东西被提交并推到了 origin/develop：

* ``tools/markitdown``            —— 一个嵌套 git 仓库被记成 gitlink（无 .gitmodules，残缺）
* ``tools/repo_to_notion/out``    —— 23 个生成清单/日志（约 2.95 MB）
* ``tools/git_develop_sync/out``  —— 5 个运行日志/报告

本脚本：
1. ``git rm -r --cached`` 把它们从索引里摘掉（文件保留在磁盘）
2. 按 ``Storage/<仓库名>/<仓内路径>`` 把它们搬到 Storage
3. 把 git_develop_sync 的工具脚本与 README 加入 git
4. 提交并推送 develop

用法::

    python fix_atriumpytools_artifacts.py --dry-run
    python fix_atriumpytools_artifacts.py --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

REPO = Path(r'D:\Repositories\Manager\AtriumPyTools')
NAME = 'AtriumPyTools'
STORAGE = Path(r'D:\Repositories\Storage')
OUT = Path(__file__).resolve().parent / 'out'

#: 从 git 索引移除并搬到 Storage 的路径
RELOCATE = ['tools/markitdown', 'tools/repo_to_notion/out', 'tools/git_develop_sync/out']
#: 加入 git（这些是真正的工具代码，应该入库）
ADD = [
    'tools/git_develop_sync/README.md',
    'tools/git_develop_sync/consolidate_develop.py',
    'tools/git_develop_sync/verify_branches.py',
    'tools/git_develop_sync/collect_deleted_branches.py',
    'tools/git_develop_sync/survey_untracked.py',
    'tools/git_develop_sync/move_untracked_to_storage.py',
    'tools/git_develop_sync/fix_atriumpytools_artifacts.py',
]
MESSAGE = ('chore: 清理误入库的生成物与子模块指针，纳入 git_develop_sync 工具\n\n'
           '- 移除 tools/markitdown 的 gitlink（无 .gitmodules 的残缺引用）\n'
           '- 移除 tools/repo_to_notion/out、tools/git_develop_sync/out 生成物\n'
           '- 上述内容改由 Storage/AtriumPyTools/ 保存\n'
           '- 新增 tools/git_develop_sync/ 分支收敛工具')


def git(*args: str, check: bool = False) -> str:
    d = subprocess.run(['git', '-C', str(REPO), *args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    out = ((d.stdout or '') + (d.stderr or '')).strip()
    if check and d.returncode != 0:
        raise SystemExit(f'git {" ".join(args)} 失败:\n{out}')
    return out


def human(n: int) -> str:
    return f'{n / 1048576:.1f} MB' if n >= 1048576 else f'{n / 1024:.0f} KB'


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='清理 AtriumPyTools 误入库的生成物')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    apply = args.apply
    print(f'{"APPLY" if apply else "DRY-RUN"}  {REPO}')
    print(f'当前 HEAD: {git("rev-parse", "--abbrev-ref", "HEAD")} '
          f'({git("rev-parse", "--short", "HEAD")})')
    print(f'与 origin/develop: 领先 {git("rev-list", "--count", "origin/develop..HEAD")} '
          f'落后 {git("rev-list", "--count", "HEAD..origin/develop")}')

    records = []
    print('\n--- 1/4 从 git 索引移除并搬到 Storage ---')
    for rel in RELOCATE:
        tracked = git('ls-files', '--', rel).splitlines()
        src, dst = REPO / rel, STORAGE / NAME / rel
        size = dir_size(src) if src.exists() else 0
        print(f'  {rel}: 索引里 {len(tracked)} 条, 磁盘 {human(size)}')
        print(f'      -> {dst}')
        if not apply:
            records.append({'rel': rel, 'tracked': len(tracked), 'dst': str(dst), 'bytes': size})
            continue
        if tracked:
            git('rm', '-r', '--cached', '--quiet', '--ignore-unmatch', '--', rel, check=True)
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                print('      ⚠ 目标已存在，跳过搬移')
            else:
                shutil.move(str(src), str(dst))
        records.append({'rel': rel, 'tracked': len(tracked), 'dst': str(dst), 'bytes': size,
                        'moved': not src.exists()})

    print('\n--- 2/4 把工具脚本加入 git ---')
    for rel in ADD:
        exists = (REPO / rel).exists()
        print(f'  {"✓" if exists else "✗"} {rel}')
        if apply and exists:
            git('add', '--', rel)

    print('\n--- 3/4 提交 ---')
    if apply:
        staged = git('diff', '--cached', '--name-status')
        print(staged or '(没有暂存内容)')
        if staged:
            git('commit', '-m', MESSAGE, check=True)
            print('已提交:', git('log', '-1', '--oneline'))
    else:
        print('  [dry-run] 将提交上述变更')

    print('\n--- 4/4 推送 develop ---')
    if apply:
        print(git('push', 'origin', 'develop'))
    else:
        print('  [dry-run] git push origin develop')

    print('\n--- 结果 ---')
    print('工作区状态:')
    print(git('status', '--porcelain') or '(干净)')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'atriumpytools_cleanup.json').write_text(json.dumps(
        {'time': datetime.now().astimezone().isoformat(timespec='seconds'),
         'apply': apply, 'relocated': records, 'added': ADD},
        ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
