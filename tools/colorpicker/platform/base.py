from abc import ABC, abstractmethod
from typing import Tuple

class ScreenCapturer(ABC):
    """屏幕像素捕获抽象接口"""

    @abstractmethod
    def capture_pixel(self, x: int, y: int) -> Tuple[float, float, float]:
        """
        捕获指定屏幕坐标 (x, y) 处的像素颜色值。

        返回值为 (R, G, B) 浮点数，范围为 [0, ∞)，
        可大于 1.0 表示 HDR 高亮。
        坐标原点：屏幕左上角 (0,0)，与操作系统一致。
        """
        pass

class GeometryProvider(ABC):
    @abstractmethod
    def mouse_location(self) -> Tuple[float, float]:
        """返回当前鼠标坐标（屏幕全局像素，原点与平台原生坐标系一致）"""
        pass