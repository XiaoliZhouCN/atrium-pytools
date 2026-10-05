"""notionsync / notionctl.py — CLI 启动器。

用法::

    python "D:\\Repositories\\Manager\\AtriumPyTools\\tools\\notionsync\\notionctl.py" doctor
    python ...\\notionctl.py cat <页面URL>
    python ...\\notionctl.py append <页面URL> --file note.md

等价于在 tools/notionsync 目录下运行 ``python -m notionsync``。
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from notionsync.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
