# -*- coding: utf-8 -*-
"""对合并后的分层结果做质量校验（不联网、不写数据）。

实现只有一份：调用 `AtriumPyTools/tools/lexicon` 里的 `lexicon.qa.verify`。
本文件只是数据目录旁的便捷入口，避免两套校验逻辑各自漂移。

等价命令：``run_lexicon.bat qa``。

注意：原版脚本读取的文件名不带数字前缀（`ielts_layered_master.csv` 等），
而实际文件已加 `0_`–`5_` 前缀，因此旧版必然 FileNotFoundError；本版已修正。
"""
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLDIR = os.path.normpath(os.path.join(HERE, "..", "..", "tools", "lexicon"))

# 附着控制台时保留控制台编码（中文 Windows 是 cp936），强行 UTF-8 会变乱码；
# 只有重定向时才统一成 UTF-8。等价命令：run_lexicon.bat qa
if not sys.stdout.isatty():
    sys.stdout.reconfigure(encoding="utf-8")
if TOOLDIR not in sys.path:
    sys.path.insert(0, TOOLDIR)

try:
    from lexicon.paths import DataPaths
    from lexicon.qa import verify
except ImportError as exc:  # pragma: no cover
    print(f"无法导入 lexicon（{exc}）")
    print(f"期望工具位置：{TOOLDIR}")
    raise SystemExit(1)

# 校验只看分层文件，不读词库；直接构造 DataPaths 以免依赖 wordbook.db
paths = DataPaths(root=Path(HERE), koolearn=Path(HERE))

print("=== 分层文件不变量校验 ===")
failures = verify(paths)
for item in failures:
    print(f"  FAIL {item}")
if failures:
    print(f"\n=== 失败 {len(failures)} 项 ===")
    raise SystemExit(1)
print("  OK   全部通过")
