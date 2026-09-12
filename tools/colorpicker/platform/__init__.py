# \Manager\ChestPyTools\tools\colorpicker\platform\__init__.py
"""平台适配层入口，根据当前操作系统加载对应实现"""

import sys
from typing import Tuple

if sys.platform == "darwin":
    from .mac.capture import MacScreenCapturer as ScreenCapturer
    from .mac.geometry import MacGeometryProvider as GeometryProvider
elif sys.platform == "win32":
    from .win.capture import WinScreenCapturer as ScreenCapturer
    from .win.geometry import WinGeometryProvider as GeometryProvider
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")

__all__ = ["ScreenCapturer"]