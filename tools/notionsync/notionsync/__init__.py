"""notionsync — Notion 多工作空间读写工具层。

设计要点
--------
* **多空间**：Notion 的凭据（PAT 或内部连接 Token）都绑定单一 workspace，
  因此要覆盖 A/B 两个空间就必须持有两枚 Token，统一由本层路由。
* **纯标准库**：只用 urllib，不引入任何第三方依赖，便于被 DSH / Trae / 本地脚本共用。
* **一份实现、三处复用**：``notionsync.client`` 被 CLI 与 MCP server 共用。

对外主要入口
------------
``NotionClient``
    单空间 REST 客户端。
``WorkspaceRegistry``
    多空间注册表，按 key 取客户端。
"""

from .client import NotionClient, NotionError
from .config import Workspace, WorkspaceRegistry, load_registry

__all__ = [
    "NotionClient",
    "NotionError",
    "Workspace",
    "WorkspaceRegistry",
    "load_registry",
]

__version__ = "0.1.0"
