"""notionsync / mcp_server.py — stdio MCP 服务器启动器。

DSH 与 Trae 都指向这个文件：

DSH（``~/.dsh/profiles/<profile>/cordis.patch.yml``）::

    - insert:
        - id: mcp-notionsync
          name: '@deepseek-ai/dsh-mcp-client'
          config:
            serverName: notionsync
            transport: stdio
            command: python
            args: ['D:\\Repositories\\Manager\\AtriumPyTools\\tools\\notionsync\\mcp_server.py']

Trae（``%APPDATA%\\Trae\\User\\mcp.json``）::

    "notionsync": {
      "command": "python",
      "args": ["D:\\Repositories\\Manager\\AtriumPyTools\\tools\\notionsync\\mcp_server.py"]
    }

服务器进程自己从 ``~/.notionsync/workspaces.json`` 读取各空间 Token，
因此不需要在 MCP 客户端配置里写任何密钥。
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from notionsync.mcp import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
