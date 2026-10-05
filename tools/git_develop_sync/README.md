# git_develop_sync

把工作区（`D:\Repositories`）里**属于某个 GitHub 账号**的仓库统一收敛到 `develop`：
合并所有未并入 develop 的分支 → 推送 → 删除其它分支，只保留 `develop` 与 `main`。

## 规则（按本次确认的口径）

1. 只处理 `origin` 指向 `github.com/XiaoliZhouCN` 的仓库（SSH 别名 `github-shirley` → `github.com`），其余跳过。
2. 有未提交改动 → 先在当前分支 `commit`，再合并进 develop（保留「改动属于哪个分支」的语义）。
3. 当前分支没有 develop → 从默认分支建 develop 并推送。
4. **所有**尚未并入 develop 的本地/远端分支（含 main）分别 merge 进 develop。
5. 合并后推送 develop；然后删除其它分支（本地 + 远端），只保留 `develop` 和 `main`。
   默认分支若是别的名字（如「主要的」）且没有 main → 改名为 main 并用 `gh` 切换 GitHub 默认分支。
6. 任何一步失败（尤其合并冲突）→ 中止该仓库、**不推送、不删分支**，留给人工处理。

## 安全设计

* **先推送再删除**：develop 没成功推上去，绝不删任何分支。
* **删除前校验**：待删分支必须是 develop 的祖先（`merge-base --is-ancestor`），否则保留并告警。
  白名单 `FORCE_DELETE` 只用于「历史被重写导致祖先关系失效、但内容确认已并入」的分支。
* **不做任何 force push**。
* 删除的分支名与用途记录在 `out/git_deleted_branches.json`，本地可用 `git reflog` 追溯。
* `--dry-run`（默认）不改动任何东西，会精确列出「需合并 / 仅删除 / 保留」三类分支。

## 用法

```powershell
$py = "C:\Users\ChestNut\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$tool = "D:\Repositories\Manager\AtriumPyTools\tools\git_develop_sync"

& $py "$tool\consolidate_develop.py" --dry-run                     # 看计划
& $py "$tool\consolidate_develop.py" --apply                       # 全量执行
& $py "$tool\consolidate_develop.py" --apply --only atrium-note    # 只处理某个仓库
& $py "$tool\consolidate_develop.py" --apply --prefer-local-on-conflict   # 冲突以本地为准

& $py "$tool\verify_branches.py"          # 核对各仓库是否只剩 develop + main
& $py "$tool\collect_deleted_branches.py" # 从日志汇总删除记录
```

可调开关（脚本顶部常量）：

| 常量 | 作用 |
| --- | --- |
| `SKIP` | 完全跳过的仓库（如上游 fork） |
| `SKIP_MERGE` | 跳过合并、仅保留的引用（内容已在 develop，只是 SHA 被重写） |
| `SKIP_MERGE_PATTERNS` | 跳过合并的引用前缀（如 `origin/ws/` 自动快照分支） |
| `FORCE_DELETE` | 免祖先校验直接删除的分支（本地/远端同名都适用） |
| `COMMIT_EXCLUDE` | 自动 commit 时要排除的路径（嵌套 git 仓库、生成物、已剔除的大文件） |

## 本次执行结果（2026-10-05）

| 仓库 | 结果 | 合并进 develop | 删除（本地 / 远端） |
| --- | --- | --- | --- |
| shirleyzh-dsh-sessions | ✅ | sessions | 2 / 2（含孤儿快照分支 `ws/eb803cad…`） |
| AtriumCppTools | ✅ | dev/basic | 3 / 1 |
| AtriumNote | ✅ | main | 1 / 1（大文件走 Git LFS） |
| AtriumPyTools | ✅ | dev/basic-lexicon、main | 4 / 3 |
| AtriumSteward | ✅ | main | 1 / 1 |
| ChestNut_Village | ⏭️ 跳过 | — | — |
| ChromaCMS | ✅ | dev/basic | 2 / 1 |
| NexusRenderer | ✅ | dev/basic、dev/basic-p1-forward-renderer、origin/main | 3 / 1 |
| OpenGLRenderer | ✅ | 无（都已合并） | 3 / 5（`主要的` 改名 main） |
| AIWorkSpace | ✅ | 无 | 0 / 0（仅新建 develop） |
| Forks/acp-ui | ⏭️ 跳过（上游 fork） | — | — |

合计：**9 个仓库收敛为 develop + main**，删除本地分支 18 个、远端分支 15 个。

不属于该账号、未处理：`DeepseekHarness/deepseek-harness`（deepseek-ai 官方）、
`tools/markitdown`（microsoft 官方）、`Projects/ShaderLearning`（没有 remote）。

## 本次踩到的坑（写脚本时注意）

1. **沙箱会挡住 SSH**：受限模式下 `ssh.exe` 报 `couldn't create signal pipe, Win32 error 5`，
   需要放开文件/网络沙箱才能 push。
2. **Git LFS 迁移会重写整条历史**：`git lfs migrate import --include-ref=<ref>` 默认重写该
   ref 的**全部**历史（连已推送的提交都会换 SHA，导致必须 force push）。
   正确姿势是加 `--exclude-ref=refs/remotes/origin/<ref>`，只重写未推送部分。
3. **`filter-branch` 会删掉工作区的文件**：剔除大文件后，被剔除的文件在磁盘上也没了（因为会
   checkout 到重写后的分支）。要先用 `git cat-file blob <sha>` 把文件写回磁盘；
   同时用 `--branches` 重写范围过大，应改成只重写 `origin/develop..develop`。
4. **`git status --porcelain --cached` 会带上未跟踪文件**，判断「有没有暂存内容」要用
   `git diff --cached --name-only`。
5. `git merge -X ours` **解决不了**改名/删除、删除/修改类冲突，需要逐条
   `git checkout --ours` / `git rm` 后再 `git commit --no-edit`。
