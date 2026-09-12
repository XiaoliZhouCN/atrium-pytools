# \Manager\ChestPyTools\tools\colorpicker\__init__.py
"""Colorpicker - 跨平台屏幕取色工具（核心库）"""

from .platform import ScreenCapturer, GeometryProvider

# 单例（懒加载）
_capturer = None
_geo = None

def _get_capturer():
    global _capturer
    if _capturer is None:
        _capturer = ScreenCapturer()
    return _capturer

def _get_geo():
    global _geo
    if _geo is None:
        _geo = GeometryProvider()
    return _geo

def sample_at(x: int, y: int) -> dict:
    """
    获取指定坐标 (x, y) 处的像素颜色（线性光强度，未标定物理单位）。
    返回: {'x': int, 'y': int, 'r': float, 'g': float, 'b': float}
    """
    r, g, b = _get_capturer().capture_pixel(x, y)
    return {"x": x, "y": y, "r": r, "g": g, "b": b}

def sample_at_cursor() -> dict:
    """
    获取鼠标当前位置的像素颜色。
    返回: {'x': float, 'y': float, 'r': float, 'g': float, 'b': float}
    """
    x, y = _get_geo().mouse_location()
    r, g, b = _get_capturer().capture_pixel(int(x), int(y))
    return {"x": x, "y": y, "r": r, "g": g, "b": b}

__all__ = ["sample_at", "sample_at_cursor", "ScreenCapturer", "GeometryProvider"]