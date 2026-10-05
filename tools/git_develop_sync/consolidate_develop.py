#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consolidate_develop.py — 把工作区里属于某个 GitHub 账号的仓库统一收敛到 develop。

规则（按用户确认的口径）
------------------------
1. 只处理 ``origin`` 指向 ``github.com/<OWNER>`` 的仓库，其它仓库跳过。
2. 有未提交改动 → 先在当前分支``commit``，再合并进 develop（保留改动的分支归属）。
3. 当前分支没有 develop → 从默认分支建 develop 并推送。
4. 所有**尚未合入 develop** 的本地/远端分支，全部分别 merge 进 develop。
5. 合并后推送 develop；然后删除其它分支（本地 + 远端），只保留 ``develop`` 和 ``main``。
   若默认分支叫别的名字（如「主要的」）且没有 main → 把它改名为 main 并切换 GitHub 默认分支。
6. 任何一步失败（尤其是合并冲突）→ 中止该仓库、不推送、不删分支，留给人工处理。

安全设计
--------
* **先推送再删除**：develop 没成功推上去，绝不删任何分支。
* **删除前校验**：待删分支必须是 develop 的祖先（``merge-base --is-ancestor``）。
* **不做任何 force push**。
* 删除前把每个分支的 SHA 记进 ``out/git_deleted_branches.json``，可按 SHA 恢复。
* ``--dry-run`` 不改动任何东西。

用法::

    python consolidate_develop.py --dry-run
    python consolidate_develop.py --apply
    python consolidate_develop.py --apply --only atrium-note
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(r'D:\Repositories')
OUT_DIR = Path(__file__).resolve().parent / 'out'
OWNER = 'XiaoliZhouCN'
ORIGIN_RE = re.compile(rf'github(?:-shirley)?[:\/]+{OWNER}/', re.I)
SKIP = {'acp-ui'}                      # 上游仓库的 fork，按用户决定跳过
KEEP = ('develop', 'main')
SKIP_DIRS = {'node_modules', 'Library', 'Temp', 'obj', 'bin', 'dist', 'build', '.next',
             '__pycache__', '.venv', 'venv', 'target', '.dsh-build', 'vendor', 'ThirdParty',
             '.vs', 'Packages', 'snapshots', 'Logs', 'UserSettings'}

#: 自动 commit 时要排除的路径（嵌套 git 仓库 / 生成物），避免产出残缺 submodule 引用
COMMIT_EXCLUDE = {
    'atrium-pytools': ['tools/markitdown', 'tools/repo_to_notion/out'],
}

COMMIT_MESSAGE = 'chore: 同步工作区改动（收敛到 develop 前自动提交）'
MERGE_MESSAGE = 'merge: {branch} -> develop'


# ---------------------------------------------------------------------------
# 基础封装
# ---------------------------------------------------------------------------
class Ctx:
    def __init__(self, apply: bool) -> None:
        self.apply = apply
        self.actions: list[str] = []
        self.deleted: dict[str, str] = {}
        self.failures: list[dict] = []

    def log(self, message: str) -> None:
        print(f'    {message}', flush=True)
        self.actions.append(message)


def run(repo: Path, *args: str, check: bool = False) -> tuple[int, str]:
    done = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                          text=True, encoding='utf-8', errors='replace')
    out = ((done.stdout or '') + (done.stderr or '')).strip()
    if check and done.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} 失败：{out}')
    return done.returncode, out


def gh(repo: Path, *args: str) -> tuple[int, str]:
    done = subprocess.run(['gh', *args], cwd=str(repo), capture_output=True,
                          text=True, encoding='utf-8', errors='replace')
    return done.returncode, ((done.stdout or '') + (done.stderr or '')).strip()


def ref_exists(repo: Path, ref: str) -> bool:
    return run(repo, 'rev-parse', '--verify', '--quiet', ref)[0] == 0


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    done = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor',
                           ancestor, descendant], capture_output=True)
    return done.returncode == 0


def local_branches(repo: Path) -> list[str]:
    rc, out = run(repo, 'for-each-ref', '--format=%(refname:short)', 'refs/heads')
    return [line.strip() for line in out.splitlines() if line.strip()]


def origin_branches(repo: Path) -> list[str]:
    rc, out = run(repo, 'for-each-ref', '--format=%(refname:short)', 'refs/remotes/origin')
    result = []
    for line in out.splitlines():
        ref = line.strip()
        # 跳过 origin/HEAD 以及某些仓库里存在的孤立 refs/remotes/origin
        if not ref.startswith('origin/') or ref.endswith('/HEAD'):
            continue
        result.append(ref.split('/', 1)[1])
    return result


def current_branch(repo: Path) -> str:
    return run(repo, 'rev-parse', '--abbrev-ref', 'HEAD')[1].strip()


def remote_url(repo: Path, name: str = 'origin') -> str:
    return run(repo, 'remote', 'get-url', name)[1].strip()


def find_repos() -> list[Path]:
    repos = []
    for path in REPO_ROOT.rglob('.git'):
        if not path.is_dir() or any(part in SKIP_DIRS for part in path.parts):
            continue
        repos.append(path.parent)
    return sorted(repos, key=lambda p: str(p).lower())


# ---------------------------------------------------------------------------
# 单仓库处理
# ---------------------------------------------------------------------------
def commit_wip(ctx: Ctx, repo: Path, name: str) -> bool:
    rc, out = run(repo, 'status', '--porcelain')
    dirty = [line for line in out.splitlines() if line.strip()]
    if not dirty:
        ctx.log('工作区干净，无需提交')
        return True
    excludes = COMMIT_EXCLUDE.get(name, [])
    ctx.log(f'提交 {len(dirty)} 项未提交改动'
            + (f'（排除 {", ".join(excludes)}）' if excludes else ''))
    if not ctx.apply:
        return True
    specs = ['.'] + [f':(exclude){p}' for p in excludes]
    rc, out = run(repo, 'add', '-A', '--', *specs)
    if rc != 0:
        ctx.failures.append({'repo': name, 'stage': 'add', 'error': out})
        return False
    rc, out = run(repo, 'status', '--porcelain', '--cached')
    if not out.strip():
        ctx.log('排除后没有可提交内容（改动都在排除范围内）')
        return True
    rc, out = run(repo, 'commit', '-m', COMMIT_MESSAGE)
    if rc != 0:
        ctx.failures.append({'repo': name, 'stage': 'commit', 'error': out})
        return False
    ctx.log(f'已提交：{out.splitlines()[0] if out else "ok"}')
    return True


def ensure_main(ctx: Ctx, repo: Path, branch: str) -> str | None:
    """保证存在 main；没有 main 但默认分支叫别的名字时改名。返回 main 的来源说明。"""
    if ref_exists(repo, 'refs/heads/main'):
        return 'main 已存在'
    if ref_exists(repo, 'refs/remotes/origin/main'):
        ctx.log('本地缺 main，从 origin/main 建一个')
        if ctx.apply:
            rc, out = run(repo, 'branch', 'main', 'origin/main')
            if rc != 0:
                ctx.failures.append({'repo': repo.name, 'stage': 'create main', 'error': out})
                return None
        return 'main 从 origin/main 创建'
    # 没有 main：把默认分支（可能是「主要的」）改名成 main
    for candidate in (branch, '主要的', 'master'):
        if candidate and ref_exists(repo, f'refs/heads/{candidate}'):
            ctx.log(f'没有 main，把 {candidate} 改名为 main 并推送')
            if ctx.apply:
                rc, out = run(repo, 'branch', '-m', candidate, 'main')
                if rc != 0:
                    ctx.failures.append({'repo': repo.name, 'stage': 'rename main', 'error': out})
                    return None
                rc, out = run(repo, 'push', '-u', 'origin', 'main')
                if rc != 0:
                    ctx.failures.append({'repo': repo.name, 'stage': 'push main', 'error': out})
                    return None
            return f'{candidate} -> main'
    ctx.log('⚠ 找不到可作 main 的分支')
    return None


def ensure_develop(ctx: Ctx, repo: Path, base: str) -> bool:
    if not ref_exists(repo, 'refs/heads/develop'):
        if ref_exists(repo, 'refs/remotes/origin/develop'):
            ctx.log('本地缺 develop，从 origin/develop 建并跟踪')
            if ctx.apply:
                rc, out = run(repo, 'checkout', '-B', 'develop', 'origin/develop')
                if rc != 0:
                    ctx.failures.append({'repo': repo.name, 'stage': 'create develop', 'error': out})
                    return False
        else:
            ctx.log(f'远端也没有 develop，从 {base} 新建并推送')
            if ctx.apply:
                rc, out = run(repo, 'checkout', '-B', 'develop', base)
                if rc != 0:
                    ctx.failures.append({'repo': repo.name, 'stage': 'create develop', 'error': out})
                    return False
                rc, out = run(repo, 'push', '-u', 'origin', 'develop')
                if rc != 0:
                    ctx.failures.append({'repo': repo.name, 'stage': 'push develop', 'error': out})
                    return False
        return True
    if ctx.apply:
        rc, out = run(repo, 'checkout', 'develop')
        if rc != 0:
            ctx.failures.append({'repo': repo.name, 'stage': 'checkout develop', 'error': out})
            return False
        if ref_exists(repo, 'refs/remotes/origin/develop'):
            rc, out = run(repo, 'merge', '--ff-only', 'origin/develop')
            if rc == 0 and 'Already up to date' not in out:
                ctx.log(f'develop 快进到 origin/develop：{out.splitlines()[0]}')
    return True


def repo_plan(repo: Path) -> dict:
    head = current_branch(repo)
    locals_ = local_branches(repo)
    remotes = origin_branches(repo)
    base = 'develop' if ref_exists(repo, 'refs/heads/develop') else None
    return {'head': head, 'local': locals_, 'remote': remotes,
            'has_develop_local': 'develop' in locals_,
            'has_develop_remote': 'develop' in remotes,
            'has_main_local': 'main' in locals_, 'has_main_remote': 'main' in remotes,
            'dirty': len([l for l in run(repo, 'status', '--porcelain')[1].splitlines() if l.strip()]),
            'base': base}


def process(ctx: Ctx, repo: Path, name: str) -> dict:
    result = {'repo': str(repo), 'name': name, 'status': 'ok', 'merged': [], 'deleted_local': [],
              'deleted_remote': [], 'kept': [], 'notes': []}
    print(f'\n>>> {name}  ({repo})', flush=True)
    url = remote_url(repo)
    print(f'    origin = {url}', flush=True)

    plan = repo_plan(repo)
    print(f"    现状：HEAD={plan['head']}  未提交={plan['dirty']}  "
          f"本地分支={plan['local']}  远端分支={plan['remote']}", flush=True)

    if not ctx.apply:
        have_dev = plan['has_develop_local'] or plan['has_develop_remote']
        base_ref = ('develop' if plan['has_develop_local']
                    else 'origin/develop' if plan['has_develop_remote'] else None)
        ctx.log(f"[dry-run] 提交 {plan['dirty']} 项未提交改动" if plan['dirty']
                else '[dry-run] 工作区干净')
        if not plan['has_main_local'] and not plan['has_main_remote']:
            ctx.log('[dry-run] 没有 main → 把默认分支改名为 main 并推送')
        elif not plan['has_main_local']:
            ctx.log('[dry-run] 本地缺 main → 从 origin/main 建')
        if not have_dev:
            ctx.log(f"[dry-run] 没有 develop → 从 {base_ref or 'main/默认分支'} 新建并推送")
        elif not plan['has_develop_local']:
            ctx.log('[dry-run] 本地缺 develop → 从 origin/develop 建')
        to_merge, to_delete, keep_merge = [], [], []
        for branch in plan['local'] + [f'origin/{b}' for b in plan['remote']]:
            if branch in ('develop', 'origin/develop'):
                continue
            is_remote = branch.startswith('origin/')
            name = branch[7:] if is_remote else branch
            if not ref_exists(repo, branch):
                continue
            merged = bool(base_ref) and is_ancestor(repo, branch, base_ref)
            if name in KEEP:
                if not merged:
                    keep_merge.append(branch)
            elif merged:
                to_delete.append(branch)
            else:
                to_merge.append(branch)
        ctx.log(f"[dry-run] 需合并进 develop（{len(to_merge)}）：{to_merge or '无'}")
        if keep_merge:
            ctx.log(f"[dry-run] 保留但尚未并入 develop，会先合并再保留：{keep_merge}")
        ctx.log(f"[dry-run] 已并入 develop，将删除（{len(to_delete)}）：{to_delete or '无'}")
        ctx.log('[dry-run] 推送 develop；保留 develop 与 main')
        result['status'] = 'dry-run'
        result['merged'] = to_merge
        result['deleted_local'] = [b for b in to_delete if not b.startswith('origin/')]
        result['deleted_remote'] = [b[7:] for b in to_delete if b.startswith('origin/')]
        return result

    # 1) fetch
    rc, out = run(repo, 'fetch', '--all', '--prune')
    if rc != 0:
        ctx.log(f'⚠ fetch 失败（继续用本地已知 ref）：{out.splitlines()[0] if out else ""}')
    plan = repo_plan(repo)

    # 2) 提交未提交改动
    if not commit_wip(ctx, repo, name):
        result['status'] = 'failed'
        return result

    # 3) 保证 main 存在
    main_note = ensure_main(ctx, repo, plan['head'])
    if main_note:
        result['notes'].append(main_note)

    # 4) 保证 develop 存在并切过去
    base = 'main' if ref_exists(repo, 'refs/heads/main') else plan['head']
    if not ensure_develop(ctx, repo, base):
        result['status'] = 'failed'
        return result

    # 5) 合并所有未并入 develop 的分支（含 main —— 保留 ≠ 跳过合并）
    candidates = [b for b in local_branches(repo) if b != 'develop']
    candidates += [f'origin/{b}' for b in origin_branches(repo) if b != 'develop']
    seen = set()
    for ref in candidates:
        if ref in seen:
            continue
        seen.add(ref)
        if not ref_exists(repo, ref):
            continue
        if is_ancestor(repo, ref, 'develop'):
            ctx.log(f'{ref} 已在 develop 里，跳过合并')
            continue
        rc, out = run(repo, 'merge', '--no-ff', '-m', MERGE_MESSAGE.format(branch=ref), ref)
        if rc != 0:
            ctx.log(f'✗ 合并 {ref} 冲突，已回滚该次合并，本仓库停止后续操作')
            print(out, flush=True)
            run(repo, 'merge', '--abort')
            ctx.failures.append({'repo': name, 'stage': f'merge {ref}', 'error': out[:800]})
            result['status'] = 'conflict'
            return result
        ctx.log(f'合并 {ref} -> develop：{out.splitlines()[0] if out else "ok"}')
        result['merged'].append(ref)

    # 6) 推送 develop（推失败就绝不删分支）
    rc, out = run(repo, 'push', '-u', 'origin', 'develop')
    if rc != 0:
        ctx.log(f'✗ 推送 develop 失败：{out[:200]}')
        ctx.failures.append({'repo': name, 'stage': 'push develop', 'error': out[:800]})
        result['status'] = 'push-failed'
        return result
    ctx.log(f'已推送 develop：{out.splitlines()[-1] if out else "ok"}')

    # 7) 如果 GitHub 默认分支不是 main/develop，先切到 main，才能删掉旧默认分支
    rc, out = gh(repo, 'repo', 'view', '--json', 'defaultBranchRef', '-q', '.defaultBranchRef.name')
    default = out.strip() if rc == 0 else ''
    if default and default not in KEEP:
        if ref_exists(repo, 'refs/heads/main'):
            rc2, out2 = gh(repo, 'repo', 'edit', '--default-branch', 'main')
            ctx.log(f'默认分支 {default} -> main：{"ok" if rc2 == 0 else out2[:120]}')
        else:
            ctx.log(f'⚠ 默认分支是 {default}，但没有 main 可切换，稍后可能删不掉')

    # 8) 删除其它分支（本地 + 远端），删前逐个校验已并入 develop
    for branch in local_branches(repo):
        if branch in KEEP or branch == 'develop':
            continue
        if not is_ancestor(repo, branch, 'develop'):
            ctx.log(f'⚠ 本地 {branch} 仍未并入 develop，保留不删')
            continue
        ctx.deleted.setdefault(f'{name}:{branch}', run(repo, 'rev-parse', branch)[1].strip())
        rc, out = run(repo, 'branch', '-D', branch)
        if rc == 0:
            ctx.log(f'删除本地分支 {branch}')
            result['deleted_local'].append(branch)
        else:
            ctx.log(f'✗ 删除本地分支 {branch} 失败：{out[:120]}')

    for branch in origin_branches(repo):
        if branch in KEEP or branch == 'develop':
            continue
        if not is_ancestor(repo, f'origin/{branch}', 'develop'):
            ctx.log(f'⚠ 远端 {branch} 仍未并入 develop，保留不删')
            continue
        ctx.deleted.setdefault(f'{name}:origin/{branch}',
                               run(repo, 'rev-parse', f'origin/{branch}')[1].strip())
        rc, out = run(repo, 'push', 'origin', '--delete', branch)
        if rc == 0:
            ctx.log(f'删除远端分支 origin/{branch}')
            result['deleted_remote'].append(branch)
        else:
            ctx.log(f'✗ 删除远端分支 {branch} 失败：{out.splitlines()[-1][:160] if out else ""}')
            result['notes'].append(f'远端 {branch} 删除失败')
    result['kept'] = [b for b in local_branches(repo)] + \
                     [f'origin/{b}' for b in origin_branches(repo)]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='把仓库收敛到 develop')
    parser.add_argument('--apply', action='store_true', help='真正执行（默认 dry-run）')
    parser.add_argument('--dry-run', action='store_true', help='只打印计划（默认行为）')
    parser.add_argument('--only', default='', help='只处理名字匹配的仓库（子串）')
    args = parser.parse_args(argv)
    ctx = Ctx(apply=args.apply)

    targets = []
    for repo in find_repos():
        url = remote_url(repo)
        if not ORIGIN_RE.search(url):
            continue
        name = repo.name
        if name in SKIP:
            print(f'跳过 {name}（{url}）')
            continue
        if args.only and args.only.lower() not in name.lower():
            continue
        targets.append((repo, name))

    print(f'{"APPLY" if ctx.apply else "DRY-RUN"}：待处理 {len(targets)} 个仓库')
    results = []
    for repo, name in targets:
        try:
            results.append(process(ctx, repo, name))
        except Exception as exc:  # noqa: BLE001
            ctx.failures.append({'repo': name, 'stage': 'exception', 'error': str(exc)})
            results.append({'repo': str(repo), 'name': name, 'status': 'exception'})
            print(f'    ✗ 异常：{exc}', flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec='seconds')
    (OUT_DIR / 'git_consolidate_report.json').write_text(json.dumps(
        {'time': stamp, 'apply': ctx.apply, 'results': results,
         'failures': ctx.failures}, ensure_ascii=False, indent=2), encoding='utf-8')
    if ctx.deleted:
        (OUT_DIR / 'git_deleted_branches.json').write_text(json.dumps(
            {'time': stamp, 'note': '删掉的分支与其 SHA，可按 SHA 恢复', 'deleted': ctx.deleted},
            ensure_ascii=False, indent=2), encoding='utf-8')

    print('\n' + '=' * 78)
    for item in results:
        print(f"  {item['name']:<26} {item['status']:<12} "
              f"合并 {len(item.get('merged', []))} 删本地 {len(item.get('deleted_local', []))} "
              f"删远端 {len(item.get('deleted_remote', []))}")
    if ctx.failures:
        print(f'\n需要人工处理 {len(ctx.failures)} 项：')
        for item in ctx.failures:
            print(f"  - {item['repo']} @ {item['stage']}: {item['error'][:120]}")
    print(f"\n报告：{OUT_DIR / 'git_consolidate_report.json'}")
    return 0 if not ctx.failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
