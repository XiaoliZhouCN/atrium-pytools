"""Windows 鼠标坐标获取 —— 使用 win32api"""

import win32api
from ..base import GeometryProvider
from typing import Tuple

class WinGeometryProvider(GeometryProvider):
    def mouse_location(self) -> Tuple[float, float]:
        cursor = win32api.GetCursorPos()
        return (float(cursor[0]), float(cursor[1]))