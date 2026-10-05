"""atriumsync — Notion ⇄ 本地（AtriumNote）⇄ 飞书 的同步层。

分三层：

* :mod:`atriumsync.paths`    目标目录、命名规则、链接规则
* :mod:`atriumsync.formats`  Markdown 承载不了的格式 → 结构化 sidecar
* :mod:`atriumsync.pull`     Notion → 本地（md / csv）

Notion 是唯一真源。
"""

from . import formats, paths, pull

__all__ = ["formats", "paths", "pull"]
__version__ = "0.1.0"
