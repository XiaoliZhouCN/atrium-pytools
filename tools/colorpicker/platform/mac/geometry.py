"""macOS 鼠标坐标获取 —— 使用 NSEvent (Quartz)"""

from Quartz import NSEvent
from ..base import GeometryProvider
from typing import Tuple

class MacGeometryProvider(GeometryProvider):
    def mouse_location(self) -> Tuple[float, float]:
        # NSEvent.mouseLocation() 返回全局屏幕坐标，原点在左下角
        # 与 Quartz 坐标系一致，可直接用于 CGDisplay / ScreenCaptureKit
        loc = NSEvent.mouseLocation()
        return (float(loc.x), float(loc.y))