#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""move_untracked_to_storage.py — 把「没有进 git」的文件搬到 Storage 下同结构目录。

规则（本次确认）
----------------
* 目标结构：``D:\\Repositories\\Storage\\<仓库名>\\<仓库内相对路径>``
* 只搬**未跟踪**（``??``）的文件/目录；被 git 跟踪的一律拒绝（搬走等于批量删除）。
* 同盘移动，秒级完成；不修改任何 .gitignore。
* 产出 ``out/storage_moves.json`` 迁移清单（源、目标、大小、时间）。

用法::

    python move_untracked_to_storage.py --dry-run
    python move_untracked_to_storage.py --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

STORAGE = Path(r'D:\Repositories\Storage')
OUT = Path(__file__).resolve().parent / 'out'

#: (仓库目录名, 仓库绝对路径, [仓库内相对路径...])
PLAN: list[tuple[str, str, list[str]]] = [
    ('NexusRenderer', r'D:\Repositories\Projects\NexusRenderer', [
        'Assets/videos/Test.mov',
        'Assets/videos/SONY-HDR-Food.mp4',
    ]),
]


def git(repo: str, *args: str) -> str:
    d = subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return ((d.stdout or '') + (d.stderr or '')).strip()


def human(n: int) -> str:
    return f'{n / 1048576:.1f} MB' if n >= 1048576 else f'{n / 1024:.1f} KB'


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='把未跟踪文件搬到 Storage')
    parser.add_argument('--apply', action='store_true', help='真正移动（默认只演练）')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    apply = args.apply

    print(f'{"APPLY" if apply else "DRY-RUN"}：Storage = {STORAGE}')
    records, problems = [], []
    for name, repo, paths in PLAN:
        print(f'\n>>> {name}  ({repo})')
        for rel in paths:
            src = Path(repo) / rel
            dst = STORAGE / name / rel
            if not src.exists():
                problems.append(f'{name}/{rel}: 源不存在')
                print(f'  ✗ 源不存在: {src}')
                continue
            tracked = git(repo, 'ls-files', '--', rel)
            if tracked:
                problems.append(f'{name}/{rel}: 已被 git 跟踪，拒绝移动')
                print(f'  ✗ 已被 git 跟踪（{len(tracked.splitlines())} 条），拒绝移动: {rel}')
                continue
            size = dir_size(src)
            print(f'  {human(size):>10}  {src}')
            print(f'           -> {dst}')
            if apply:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    problems.append(f'{name}/{rel}: 目标已存在，跳过')
                    print('           ✗ 目标已存在，跳过')
                    continue
                shutil.move(str(src), str(dst))
                moved = dst.exists() and dir_size(dst) == size
                print(f'           {"✅ 已移动" if moved else "❌ 移动后大小不一致"}')
                if not moved:
                    problems.append(f'{name}/{rel}: 移动后大小不一致')
                records.append({'repo': name, 'rel': rel, 'src': str(src), 'dst': str(dst),
                                'bytes': size, 'ok': moved})
            else:
                records.append({'repo': name, 'rel': rel, 'src': str(src), 'dst': str(dst),
                                'bytes': size, 'ok': None})

    if apply:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'storage_moves.json').write_text(json.dumps(
            {'time': datetime.now().astimezone().isoformat(timespec='seconds'),
             'storage': str(STORAGE), 'moves': records, 'problems': problems},
            ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"\n清单：{OUT / 'storage_moves.json'}")
    total = sum(r['bytes'] for r in records)
    print(f'\n{"已" if apply else "将"} 移动 {len(records)} 项，共 {human(total)}；问题 {len(problems)}')
    for item in problems:
        print('  -', item)
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
