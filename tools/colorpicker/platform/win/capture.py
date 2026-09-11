"""Windows 屏幕捕获实现。

这里使用原生 Win32 GDI 的 ``GetPixel`` 读取桌面设备上下文中的单个像素。
对于命令行单点取色场景，这个方案足够直接，也避免了额外的图像处理依赖。
"""

from ctypes import WinDLL, wintypes
from typing import Tuple

from ..base import ScreenCapturer

user32 = WinDLL("user32", use_last_error=True)
gdi32 = WinDLL("gdi32", use_last_error=True)

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = wintypes.INT

gdi32.GetPixel.argtypes = [wintypes.HDC, wintypes.INT, wintypes.INT]
gdi32.GetPixel.restype = wintypes.COLORREF

_DPI_AWARENESS_SET = False


def _ensure_dpi_awareness() -> None:
    """尽量启用 DPI 感知，避免高缩放显示器上的坐标偏移。"""
    global _DPI_AWARENESS_SET
    if _DPI_AWARENESS_SET:
        return

    try:
        shcore = WinDLL("shcore", use_last_error=True)
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        shcore.SetProcessDpiAwareness(2)
    except OSError:
        # 旧系统没有 shcore.dll 时退回到旧接口。
        user32.SetProcessDPIAware()
    except Exception:
        # 已设置过 DPI 或当前环境不允许时，继续执行即可。
        pass

    _DPI_AWARENESS_SET = True


class WinScreenCapturer(ScreenCapturer):
    """基于 Win32 GDI 的 Windows 像素捕获器。"""

    def capture_pixel(self, x: int, y: int) -> Tuple[float, float, float]:
        """读取指定屏幕坐标的像素，并返回归一化后的 RGB。"""
        _ensure_dpi_awareness()

        desktop_dc = user32.GetDC(None)
        if not desktop_dc:
            raise RuntimeError("无法获取桌面设备上下文。")

        try:
            color_ref = gdi32.GetPixel(desktop_dc, x, y)
            if color_ref == 0xFFFFFFFF:
                raise ValueError(f"无法读取坐标 ({x}, {y}) 的像素。")

            red = color_ref & 0xFF
            green = (color_ref >> 8) & 0xFF
            blue = (color_ref >> 16) & 0xFF
            return (red / 255.0, green / 255.0, blue / 255.0)
        finally:
            user32.ReleaseDC(None, desktop_dc)
