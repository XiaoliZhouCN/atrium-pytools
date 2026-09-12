# PyTools/tools/colorpicker/profile.py
"""
预设配置：定义 HDR/SDR 的校正参数
"""
from dataclasses import dataclass
from typing import Optional

@dataclass
class ColorProfile:
    """色彩校正配置"""
    name: str
    mapping: int          # -1 原生, -2 EDR 线性
    correction: int       # -1 P3 D65 sRGB, -2 P3-ST 2084 γ2.2
    p3_strategy: int      # -1 转 P3, -2 不转 P3
    primaries: int        # -1 709->P3, -2 不变
    headroom: float       # EDR 亮度倍数 (1000 / SDR白)
    sdr_white_nits: float # SDR 白亮度 (nits)

# 预设定义
PRESETS = {
    "hdr": ColorProfile(
        name="HDR",
        mapping=-2,
        correction=-2,
        p3_strategy=-2,
        primaries=-2,
        headroom=10.0,      # 1000 / 100
        sdr_white_nits=100.0
    ),
    "sdr": ColorProfile(
        name="SDR",
        mapping=-1,
        correction=-1,
        p3_strategy=-2,
        primaries=-1,
        headroom=1000/203,  # ~4.926
        sdr_white_nits=203.0
    )
}

def get_preset(name: str) -> Optional[ColorProfile]:
    """根据名称获取预设，默认返回 HDR"""
    return PRESETS.get(name.lower(), PRESETS["hdr"])