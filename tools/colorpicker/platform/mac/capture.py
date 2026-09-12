"""macOS 屏幕捕获 —— 使用 ScreenCaptureKit (HDR 支持，1x1 精确采样)"""

import Cocoa
import dispatch
from Quartz import (
    CGPreflightScreenCaptureAccess,
    kCGColorSpaceExtendedLinearDisplayP3,
    CGRectMake,
)
from ScreenCaptureKit import (
    SCShareableContent,
    SCContentFilter,
    SCStreamConfiguration,
    SCScreenshotManager,
)
from typing import Tuple, Optional
from ..base import ScreenCapturer


class MacScreenCapturer(ScreenCapturer):
    """基于 ScreenCaptureKit 的精确像素捕获器，支持 HDR"""

    def __init__(self):
        self._shareable_content = None

    def _get_shareable_content(self):
        """获取或缓存共享内容（显示器列表）"""
        if self._shareable_content is not None:
            return self._shareable_content

        # 屏幕录制权限检查
        if not CGPreflightScreenCaptureAccess():
            raise RuntimeError(
                "Screen recording permission not granted. "
                "Please enable in System Settings → Privacy & Security → Screen Recording."
            )

        sem = dispatch.dispatch_semaphore_create(0)
        result = None

        def callback(content, error):
            nonlocal result
            result = (content, error)
            dispatch.dispatch_semaphore_signal(sem)

        SCShareableContent.getShareableContentWithCompletionHandler_(callback)
        dispatch.dispatch_semaphore_wait(sem, dispatch.DISPATCH_TIME_FOREVER)

        content, err = result
        if err:
            raise RuntimeError(f"Failed to get shareable content: {err}")
        if content is None:
            raise RuntimeError("No shareable content returned")

        self._shareable_content = content
        return content

    def _find_display_for_point(self, x: float, y: float):
        """根据全局坐标查找包含该点的显示器，返回 (display, local_x, local_y)"""
        content = self._get_shareable_content()
        for display in content.displays():
            frame = display.frame()
            # frame 是 CGRect，原点在左下角，与 NSEvent 坐标一致
            if (frame.origin.x <= x <= frame.origin.x + frame.size.width and
                frame.origin.y <= y <= frame.origin.y + frame.size.height):
                local_x = x - frame.origin.x
                local_y = y - frame.origin.y
                return display, local_x, local_y

        # 如果找不到（如坐标在菜单栏外），回退到主显示器
        main = content.displays()[0]
        return main, x, y

    def capture_pixel(self, x: int, y: int) -> Tuple[float, float, float]:
        """
        捕获屏幕 (x, y) 处的 1x1 像素。
        x, y 应为全局屏幕坐标（原点左下角，与 NSEvent 一致）。
        """
        # 1. 找到对应的显示器
        display, local_x, local_y = self._find_display_for_point(float(x), float(y))

        # 2. 创建该显示器的过滤器（排除所有窗口，只捕获桌面/背景）
        filter = SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])

        # 3. 配置 1x1 采样，并指定线性 P3 色彩空间
        config = SCStreamConfiguration.alloc().init()
        config.setSourceRect_(CGRectMake(local_x, local_y, 1.0, 1.0))
        config.setWidth_(1)
        config.setHeight_(1)
        config.setColorSpaceName_(kCGColorSpaceExtendedLinearDisplayP3)

        # 4. 异步截屏
        sem = dispatch.dispatch_semaphore_create(0)
        result_img = None
        result_err = None

        def screenshot_handler(img, error):
            nonlocal result_img, result_err
            result_img = img
            result_err = error
            dispatch.dispatch_semaphore_signal(sem)

        SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
            filter, config, screenshot_handler
        )
        dispatch.dispatch_semaphore_wait(sem, dispatch.DISPATCH_TIME_FOREVER)

        if result_err:
            raise RuntimeError(f"Capture failed: {result_err}")

        if result_img is None:
            return (0.0, 0.0, 0.0)

        # 5. 读取像素（因为是 1x1，直接取 (0,0)）
        bitmap = Cocoa.NSBitmapImageRep.alloc().initWithCGImage_(result_img)
        pixel = bitmap.colorAtX_y_(0, 0)
        r = pixel.redComponent()
        g = pixel.greenComponent()
        b = pixel.blueComponent()
        return (float(r), float(g), float(b))