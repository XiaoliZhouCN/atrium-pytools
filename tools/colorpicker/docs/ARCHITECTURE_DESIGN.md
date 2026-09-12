# Colorpicker 跨平台重构策划案

> **文档版本**：v1.0\
> **创建日期**：2026-09-03\
> **目标路径**：`PyTools/docs/colorpicker_plan.md`

***

## 一、项目概述

### 1.1 背景

Colorpicker 当前为 macOS 原生取色工具，支持 HDR 采样、EDR 线性读数、定位点自动取色、Excel 报告导出及 EDR 误差对比等功能。现需将其改造为**跨平台工具**（macOS + Windows），并纳入 PyTools 工具库规范。

### 1.2 目标

- 实现 Colorpicker 在 **macOS** 和 **Windows** 双端运行
- 保持现有功能完整性，精度优先、HDR 支持优先
- 核心算法 **100% 跨平台复用**，平台差异仅限取色与界面适配层
- 符合 PyTools 规范：统一 CLI 入口，核心模块通过 `__init__.py` 暴露 API

***

## 二、设计目标

| 目标         | 优先级 | 说明                                         |
| ---------- | --- | ------------------------------------------ |
| **取色精度**   | P0  | 全链路保持 32-bit float，避免 8-bit 量化丢失           |
| **HDR 支持** | P0  | macOS 支持 EDR 线性读数；Windows 支持 HDR 捕获与浮点数据读取 |
| **功能完整性**  | P1  | 定位、自动取色、EDR 对比、Excel 导出等功能双端一致             |
| **代码复用率**  | P1  | 核心算法模块跨平台共享，复用率 ≥ 80%                      |
| **性能**     | P2  | 采色间隔 ≤ 100 ms，支持 80 ms 高频采样                |
| **开发成本**   | P2  | 单套代码库，平台适配层独立，最小化重复开发                      |

***

## 三、技术栈选型

### 3.1 通用（跨平台）

| 组件       | 技术                           | 说明            |
| -------- | ---------------------------- | ------------- |
| 语言       | Python 3.11+                 | 符合 PyTools 规范 |
| 包管理      | `pyproject.toml` + Hatchling | 标准 Python 打包  |
| 色彩算法     | 纯 Python + `numpy`           | 矩阵运算与 TRC 公式  |
| Excel 读写 | `openpyxl`                   | 自动化取色结果写入     |
| 配置管理     | `json`                       | 定位、预设、对比配置    |

### 3.2 macOS 平台

| 组件     | 技术                                | 说明               |
| ------ | --------------------------------- | ---------------- |
| 屏幕捕获   | **ScreenCaptureKit** (via PyObjC) | 原生 HDR 捕获，返回浮点像素 |
| 坐标映射   | PyObjC + Quartz / AppKit          | 鼠标定位、显示坐标系转换     |
| GUI 框架 | **PyObjC + Cocoa**                | 保持现有原生 HUD/对比窗口  |
| 主循环    | `NSApplication`                   | 现有实现保留           |

### 3.3 Windows 平台

| 组件     | 技术                                                                   | 说明                     |
| ------ | -------------------------------------------------------------------- | ---------------------- |
| 屏幕捕获   | **DXGI Desktop Duplication API** (via `d3dshot` 或 `windows-capture`) | 高性能 HDR 捕获，16-bit 浮点数据 |
| 坐标映射   | `win32api.GetCursorPos()`                                            | 鼠标定位                   |
| GUI 框架 | **Tkinter**                                                          | Python 内置，零额外依赖，轻量 HUD |
| 主循环    | Tkinter 主循环 + 全局热键 (via `keyboard`/`pynput`)                         | 替代 NSApplication       |

***

## 四、整体架构

### 4.1 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│                     CLI 入口 (cli.py)                       │
│              根据 sys.platform 路由到对应平台              │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                  platform/__init__.py                      │
│              抽象工厂：返回对应平台实现实例                 │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
┌────────▼────────┐ ┌─────────▼────────┐ ┌────────▼────────┐
│   核心算法层     │ │    配置/I/O层     │ │   平台适配层     │
│   core.py       │ │   config/io.py   │ │  platform/      │
│   profile.py    │ │   clipboard.py   │ │  mac/win/       │
└─────────────────┘ └──────────────────┘ └─────────────────┘
```

### 4.2 数据流

```
鼠标/定位点坐标
    │
    ▼
[平台适配层] GeometryProvider.mouse_location()
    │
    ▼
[平台适配层] ScreenCapturer.capture_pixel(x, y) → (R, G, B) float
    │
    ▼
[核心算法层] 色彩空间转换、TRC 解码、headroom 标定
    │
    ▼
[配置/输出层] 显示位数格式化 → HUD / 剪贴板 / Excel
```

### 4.3 模块依赖关系

```
cli.py
  ├── platform/__init__.py
  │   ├── base.py           (抽象接口)
  │   ├── mac/
  │   │   ├── capture.py    → ScreenCaptureKit
  │   │   ├── geometry.py   → PyObjC
  │   │   └── gui.py        → PyObjC + Cocoa
  │   └── win/
  │       ├── capture.py    → DXGI Desktop Duplication
  │       ├── geometry.py   → win32api
  │       └── gui.py        → Tkinter
  ├── core.py               (纯算法，无平台依赖)
  ├── profile.py            (预设配置，无平台依赖)
  ├── config.py             (JSON 配置加载)
  ├── io.py                 (Excel/JSON 读写)
  └── clipboard.py          (文本格式化)
```

***

## 五、模块职责与接口定义

### 5.0 性能关键模块的 C++ 加速策略（可选增强层）

#### 5.0.1 设计目标

在保证跨平台兼容性的前提下，为色彩计算核心提供接近原生的执行性能。将 **色彩空间转换（RGB↔XYZ↔CIELAB）**、**TRC 编解码（PQ EOTF / sRGB 解码）** 和 **色域矩阵乘法** 等高密度数学运算下沉至 C++ 层，Python 仅作为轻量级调度与数据包装器。

#### 5.0.2 实现方案（双轨制）

采用 **“Python 主包 + C++ 动态链接库（.pyd/.so）”** 的混合架构：

| 层级 | 文件 | 职责 | 语言 |
| :--- | :--- | :--- | :--- |
| **暴露层** | `tools/colorpicker/__init__.py` | 暴露 `pick_screen_color()` 等顶层 API | Python |
| **包装层** | `tools/colorpicker/core.py` | **智能路由**：优先 `import _colorpicker_cpp`；若导入失败（编译环境缺失），则回退到纯 Python + NumPy 实现（`core_py_fallback.py`） | Python |
| **加速层** | `tools/colorpicker/_core/` | 编译产物存放目录（`.gitignore`），内含 CMake 构建脚本 | C++ (pybind11) |
| **算法内核** | `src/color_math.cpp` | 矩阵乘法、PQ EOTF、色域转换等纯数学函数 | C++ |

#### 5.0.3 接口一致性保证

C++ 扩展导出的函数签名必须与纯 Python 回退版本**完全等价**：

```cpp
// C++ 侧 (pybind11 导出)
py::array_t<float> rgb_to_xyz(py::array_t<float> rgb, py::array_t<float> primaries);
py::array_t<float> pq_eotf(py::array_t<float> e);
```

```python
# Python 回退版本 (core_py_fallback.py)
def rgb_to_xyz(rgb: np.ndarray, primaries: np.ndarray) -> np.ndarray:
    # 纯 Python + NumPy 实现
    pass
```

`core.py` 中的路由逻辑：

```python
# core.py
try:
    from _colorpicker_cpp import rgb_to_xyz, pq_eotf, xyz_to_rgb  # 尝试加载 C++ 扩展
    _USE_CPP = True
except ImportError:
    from .core_py_fallback import rgb_to_xyz, pq_eotf, xyz_to_rgb  # 回退纯 Python
    _USE_CPP = False
    warnings.warn("C++ 扩展未编译，使用纯 Python 回退（性能较低）")
```

#### 5.0.4 构建集成

- **CMake 构建脚本**：位于 `tools/colorpicker/CMakeLists.txt`，使用 `pybind11` 生成 `_colorpicker_cpp.pyd`（Windows）或 `.so`（macOS/Linux）。
- **开发环境**：在 `requirements.txt` 中添加 `pybind11>=2.10`；在 `pyproject.toml` 中通过 `[tool.setuptools.package-data]` 包含编译产物（可选）。
- **CI/发布**：预编译 Windows (x64) / macOS (arm64) 的 `.pyd` / `.so` 文件随 PyTools 一同分发，用户无需本地安装 C++ 编译器即可获得加速。

#### 5.0.5 预期性能收益

| 操作 | 纯 Python + NumPy | C++ 扩展 (O2 优化) | 提升倍数 |
| :--- | :--- | :--- | :--- |
| 单像素 RGB→XYZ 转换 | ~2.5 µs | ~0.15 µs | **~16x** |
| 批量 1000 点色彩空间转换 | ~4.2 ms | ~0.3 ms | **~14x** |
| PQ EOTF (单像素) | ~1.8 µs | ~0.1 µs | **~18x** |

*（注：实际提升取决于矩阵规模与编译器优化等级）*

#### 5.0.6 阶段规划

| 阶段 | 内容 | 优先级 |
| :--- | :--- | :--- |
| **Phase 1（当前）** | 纯 Python + NumPy 实现，确保算法正确性 | P0 |
| **Phase 1.5（可选增强）** | 引入 pybind11 框架，将最热路径（RGB↔XYZ、PQ EOTF）迁移至 C++，保留纯 Python 回退开关 | P1（建议在 Phase 3 Windows 适配期间同步进行） |
| **Phase 2（未来）** | 将 DXGI 捕获到的 16-bit 浮点原始数据直接通过 `memoryview` 传递给 C++ 层，实现零拷贝处理 | P2 |

### 5.1 `core.py` — 核心算法

> **职责**：所有与平台无关的色彩数学。  
> **实现方式**：采用 **“C++ 加速 + Python 回退”** 双轨制（详见 §5.0）。`core.py` 作为智能路由层，优先导入编译好的 `_colorpicker_cpp` 扩展；若编译产物缺失，则无缝降级至纯 Python + NumPy 实现，保证开发环境零额外依赖即可运行。

**对外接口**：

| 函数签名                                                   | 说明             |
| ------------------------------------------------------ | -------------- |
| `rgb_to_xyz(rgb, primaries) → xyz`                     | 线性 RGB → XYZ   |
| `xyz_to_rgb(xyz, primaries) → rgb`                     | XYZ → 线性 RGB   |
| `matrix_709_to_p3() → 3x3`                             | 709→P3 转换矩阵    |
| `srgb_decode(v) → l`                                   | sRGB 编码 → 线性光  |
| `srgb_encode(l) → v`                                   | 线性光 → sRGB 编码  |
| `pq_eotf(e) → n`                                       | PQ 码 → 线性 nits |
| `pq_oetf(n) → e`                                       | 线性 nits → PQ 码 |
| `pure_power_decode(v, gamma) → l`                      | 纯幂函数解码         |
| `compute_hdr_tolerance(ref, test, W, tolerance) → str` | HDR 容差判定       |
| `compute_sdr_tolerance(ref, test, tolerance) → str`    | SDR 容差判定       |

### 5.2 `platform/base.py` — 抽象接口层

**职责**：定义平台适配层必须实现的接口

```python
from abc import ABC, abstractmethod

class ScreenCapturer(ABC):
    @abstractmethod
    def capture_pixel(self, x: int, y: int, rect_size: int = 1) -> tuple[float, float, float]:
        """返回 (R, G, B) float，尽可能保留原始编码 (可>1.0)"""
        pass

class GeometryProvider(ABC):
    @abstractmethod
    def mouse_location(self) -> tuple[float, float]:
        """返回当前鼠标位置（平台原生坐标）"""
        pass

class GUIApp(ABC):
    @abstractmethod
    def run(self, config):
        """启动主循环"""
        pass

    @abstractmethod
    def update_hud(self, sample, coord):
        """刷新 HUD 显示"""
        pass

    @abstractmethod
    def register_hotkey(self, key, callback):
        """注册全局热键"""
        pass
```

### 5.3 `platform/mac/` — macOS 实现

| 模块            | 职责                                   | 关键依赖                     |
| ------------- | ------------------------------------ | ------------------------ |
| `capture.py`  | ScreenCaptureKit HDR 捕获，回退 CGDisplay | PyObjC, ScreenCaptureKit |
| `geometry.py` | AppKit 鼠标位置，Quartz 坐标映射              | PyObjC, AppKit, Quartz   |
| `gui.py`      | HUD 窗口，对比面板 (Cocoa)                  | PyObjC, Cocoa            |
| `app.py`      | NSApplication 主循环，⌘C/⌘点击热键           | PyObjC                   |

### 5.4 `platform/win/` — Windows 实现

| 模块            | 职责                              | 关键依赖                          |
| ------------- | ------------------------------- | ----------------------------- |
| `capture.py`  | DXGI Desktop Duplication API 捕获 | `windows-capture` / `d3dshot` |
| `geometry.py` | win32api 鼠标位置                   | `pywin32`                     |
| `gui.py`      | Tkinter HUD 窗口，对比面板             | Tkinter                       |
| `app.py`      | Windows 消息循环，全局热键               | `keyboard` / `pynput`         |

### 5.5 `profile.py` — 预设配置

**职责**：管理 HDR/SDR 预设参数

| 接口                                      | 说明                       |
| --------------------------------------- | ------------------------ |
| `get_profile(mode: str) → ColorProfile` | 根据模式名返回预设（HDR/SDR）       |
| `ColorProfile`                          | 包含：映射、显示校正、P3策略、headroom |

### 5.6 `config.py` — 配置管理

**职责**：加载 JSON 配置文件，解析路径模板

| 接口                                           | 说明         |
| -------------------------------------------- | ---------- |
| `load_pick_mode_config() → dict`             | 加载取色模式配置   |
| `load_compare_config() → dict`               | 加载对比配置     |
| `expand_path_template(template, vars) → str` | 展开路径模板变量   |
| `load_points(json_path) → list`              | 加载定位点 JSON |
| `save_points(points, json_path)`             | 保存定位点 JSON |

### 5.7 `io.py` — 文件 I/O

| 接口                                   | 说明                |
| ------------------------------------ | ----------------- |
| `write_excel(data, path, digits)`    | 写入自动取色结果          |
| `write_compare_excel(results, path)` | 写入对比结果表           |
| `read_excel_data(path) → DataFrame`  | 读取已存在的 Excel 用于对比 |

### 5.8 `clipboard.py` — 剪贴板格式化

| 接口                                             | 说明       |
| ---------------------------------------------- | -------- |
| `format_copy_text(sample, digits, mode) → str` | 生成复制文本   |
| `copy_to_clipboard(text)`                      | 跨平台剪贴板写入 |

***

## 六、目录结构

```
PyTools/
├── pyproject.toml
├── requirements.txt
├── README.md
│
├── tools/
│   └── colorpicker/
│       ├── __init__.py                    # 暴露顶层 API
│       ├── cli.py                         # 统一 CLI 入口
│       │
│       ├── core.py                        # 核心算法 (跨平台)
│       ├── profile.py                     # 预设配置 (跨平台)
│       ├── config.py                      # 配置管理 (跨平台)
│       ├── io.py                          # Excel/JSON I/O (跨平台)
│       ├── clipboard.py                   # 剪贴板格式化 (跨平台)
│       │
│       ├── platform/
│       │   ├── __init__.py                # 根据 sys.platform 路由
│       │   ├── base.py                    # 抽象基类
│       │   │
│       │   ├── mac/
│       │   │   ├── __init__.py
│       │   │   ├── capture.py             # ScreenCaptureKit
│       │   │   ├── geometry.py            # AppKit → Quartz
│       │   │   ├── gui.py                 # PyObjC HUD
│       │   │   └── app.py                 # NSApplication
│       │   │
│       │   └── win/
│       │       ├── __init__.py
│       │       ├── capture.py             # DXGI Desktop Duplication
│       │       ├── geometry.py            # win32api 坐标
│       │       ├── gui.py                 # Tkinter HUD
│       │       └── app.py                 # Windows 消息循环
│       │
│       └── tests/
│           ├── test_core.py               # 纯算法单元测试
│           ├── test_config.py
│           ├── test_io.py
│           └── platform/
│               ├── test_mac/
│               └── test_win/ (mock 实现)
│
├── shared/                                # 工具间共享（极少使用）
│   └── ...
│
├── data/
│   └── colorpicker/                       # 运行时数据目录
│       ├── pick_mode_config.json
│       ├── compare_mode_config.json
│       ├── points.json
│       └── color_result/
│
└── launcher/
    ├── run_colorpicker.sh                 # macOS 启动脚本
    └── run_colorpicker.bat                # Windows 启动脚本
```

***

## 七、关键功能实现策略

### 7.1 取色精度对齐

| 平台           | 原始精度                     | 进入核心层前处理                |
| ------------ | ------------------------ | ----------------------- |
| macOS        | 32-bit float (ColorSync) | 直接传递                    |
| Windows      | 16-bit float (DXGI)      | 转换为 32-bit float 传递     |
| Windows (回退) | 8-bit (GetPixel)         | 归一化为 float: `v / 255.0` |

**统一目标**：核心层接收的始终是 `(R, G, B)` float，可 `>1.0` 表示 HDR。

### 7.2 HDR 支持

| 平台      | HDR 捕获方式                                     | 输出格式                                           |
| ------- | -------------------------------------------- | ---------------------------------------------- |
| macOS   | ScreenCaptureKit `.hdrImage`                 | `Extended sRGB` 或 `Extended Linear Display P3` |
| Windows | DXGI 桌面复制 + `DXGI_FORMAT_R16G16B16A16_FLOAT` | 16-bit 浮点线性值                                   |

**ColorSync 等效处理**：

- Windows 端无 ColorSync，需在 `core.py` 中实现等效的：
  - TRC 解码（sRGB / PQ EOTF）
  - 原色矩阵（BT.709 → Display P3 转换）
  - headroom 标定（`1.0 = 100 nits`）

### 7.3 坐标系统统一

| 平台      | 原生坐标原点     | 转换后统一             |
| ------- | ---------- | ----------------- |
| macOS   | AppKit: 左下 | 内部统一使用 Quartz: 左上 |
| Windows | 左上         | 直接使用              |

**统一策略**：平台适配层负责将坐标转换为**屏幕像素坐标 (x, y)**，传入核心层。

### 7.4 GUI 差异处理

| 功能     | macOS                               | Windows                         |
| ------ | ----------------------------------- | ------------------------------- |
| HUD 窗口 | PyObjC + Cocoa 原生                   | Tkinter 浮窗                      |
| 实时刷新   | `NSTimer` 80ms                      | `after()` 80ms                  |
| 热键 ⌘C  | NSApplication 回调                    | `keyboard.add_hotkey('ctrl+c')` |
| 定位 ⌘点击 | `NSEvent.addGlobalMonitorForEvents` | `keyboard.on_press` 监听          |
| 对比窗口   | 独立 NSWindow                         | 独立 Tkinter Toplevel             |

### 7.5 Excel 输出一致性

| 功能    | 双端一致 | 说明                              | <br />      | <br />                        |
| ----- | ---- | ------------------------------- | :---------- | :---------------------------- |
| 文件名格式 | ✅    | \`\[版本\_]\[test\_app]\_\[hdr    | sdr]\_\[img | vid]_\[name]_\[format].xlsx\` |
| 列定义   | ✅    | A:ID, B:名称, C:位置, D:R, E:G, F:B | <br />      | <br />                        |
| 显示位数  | ✅    | 4/12 位，双端同步                     | <br />      | <br />                        |
| 对比表格式 | ✅    | 同现有定义                           | <br />      | <br />                        |

***

## 八、开发阶段规划

### Phase 1：核心层抽取（Week 1-2）

| 任务                                    | 产出    | 验收标准              |
| ------------------------------------- | ----- | ----------------- |
| 从现有代码抽取 `core.py`                     | 纯算法模块 | 所有矩阵、TRC 函数单元测试通过 |
| 定义 `platform/base.py`                 | 抽象接口  | 接口文档完整            |
| 抽取 `config.py`, `io.py`, `profile.py` | 跨平台模块 | 可独立运行             |

### Phase 2：macOS 适配层迁移（Week 3-4）

| 任务                                          | 产出            | 验收标准                                |
| ------------------------------------------- | ------------- | ----------------------------------- |
| 迁移现有 capture/geometry/gui 到 `platform/mac/` | 适配层           | 现有功能与之前完全一致                         |
| 更新导入路径                                      | 可运行的 macOS 版本 | `python -m colorpicker` 正常启动        |
| 集成到 `platform/__init__.py`                  | 路由逻辑          | `sys.platform == 'darwin'` 走 mac 分支 |

### Phase 3：Windows 适配层实现（Week 5-8）

| 任务                         | 产出                         | 验收标准                               |
| -------------------------- | -------------------------- | ---------------------------------- |
| Windows 取色实现 (DXGI)        | `platform/win/capture.py`  | 可获取 HDR 像素浮点值                      |
| Windows 坐标映射               | `platform/win/geometry.py` | 鼠标位置准确                             |
| Windows GUI (Tkinter)      | `platform/win/gui.py`      | HUD 显示，热键响应                        |
| 集成到 `platform/__init__.py` | 路由逻辑                       | `sys.platform == 'win32'` 走 win 分支 |

### Phase 4：功能对齐与测试（Week 9-10）

| 任务       | 产出         | 验收标准                 |
| -------- | ---------- | -------------------- |
| 定位点自动取色  | Windows 实现 | Excel 输出格式与 macOS 一致 |
| EDR 对比面板 | Windows 实现 | 对比表/图可正常生成           |
| 配置文件路径展开 | Windows 适配 | 路径模板双端可用             |
| 跨平台集成测试  | 测试报告       | 双端功能清单全部通过           |

### Phase 5：文档与发布（Week 11-12）

| 任务                  | 产出   | 验收标准              |
| ------------------- | ---- | ----------------- |
| 编写 `README.md`      | 用户文档 | 含安装、使用、配置说明       |
| 更新 `FUNCTION.md`    | 技术文档 | 反映新架构与 Windows 支持 |
| 编写 `DEVELOPMENT.md` | 开发文档 | 含模块说明、调试指南        |
| 打包发布                | 分发包  | `pip install` 可用  |

***

## 九、测试策略

### 9.1 单元测试 (`pytest`)

| 模块          | 测试内容              | 覆盖目标  |
| ----------- | ----------------- | ----- |
| `core.py`   | 矩阵精度、TRC 编解码、容差计算 | ≥ 95% |
| `config.py` | JSON 解析、路径模板展开    | ≥ 90% |
| `io.py`     | Excel 读写、数据格式正确   | ≥ 85% |

### 9.2 集成测试

| 场景             | macOS | Windows |
| -------------- | ----- | ------- |
| 单点取色 HUD 显示    | ✅     | ✅       |
| ⌘C / Ctrl+C 复制 | ✅     | ✅       |
| 定位点加载与自动取色     | ✅     | ✅       |
| Excel 报告生成     | ✅     | ✅       |
| EDR 对比面板       | ✅     | ✅       |
| HDR 内容取色（>1.0） | ✅     | ✅       |

### 9.3 精度对比测试

| 测试         | 方案                                            |
| ---------- | --------------------------------------------- |
| 双端同一屏幕取色对比 | 在同一台 Windows 机器上，对比 DXGI 与 macOS 取色器对同一测试图的读数 |
| 回归测试       | 与旧版 macOS Colorpicker 对比，误差 ≤ 1e-5            |
| HDR 测试     | 对标准 HDR 测试图，对比双端线性 EDR 读数                     |

***

## 十、风险与应对

| 风险                                | 概率 | 影响 | 应对措施                                       |
| --------------------------------- | -- | -- | ------------------------------------------ |
| ScreenCaptureKit 在 Python 中稳定调用困难 | 中  | 高  | 预留 PyObjC 回退路径，评估 `screen-capturekit` 第三方库 |
| DXGI 实现复杂度高                       | 高  | 高  | 优先采用 `windows-capture` (Rust) 库，降低开发成本     |
| Windows HDR 色彩空间处理与 macOS 不一致     | 中  | 中  | 在 `core.py` 中实现完整色域/TRC 转换，对齐 ColorSync 行为 |
| Tkinter HUD 性能不足                  | 低  | 低  | 改用 PyQt 或 win32 GUI 备选                     |
| 权限/沙盒差异                           | 中  | 中  | 提前测试 Windows 屏幕捕获权限要求                      |

***

## 十一、交付物

| 交付物          | 格式                    | 说明                                |
| ------------ | --------------------- | --------------------------------- |
| 源代码          | Python 包              | `PyTools/tools/colorpicker/` 完整目录 |
| macOS 启动脚本   | `run_colorpicker.sh`  | 可直接运行                             |
| Windows 启动脚本 | `run_colorpicker.bat` | 可直接运行                             |
| 用户文档         | `README.md`           | 安装、配置、使用说明                        |
| 技术文档         | `FUNCTION.md`         | 算法公式、模块说明                         |
| 开发文档         | `DEVELOPMENT.md`      | 架构、调试、扩展指南                        |
| 测试报告         | `test_report.html`    | 单元测试 + 集成测试结果                     |
| 示例配置         | JSON 模板               | 取色配置、对比配置、定位点示例                   |

***

## 十二、附录

### A. 依赖清单

**通用依赖**

```
numpy>=1.24.0
openpyxl>=3.1.0
```

**macOS 依赖**

```
pyobjc-core>=9.0
pyobjc-framework-Cocoa>=9.0
pyobjc-framework-Quartz>=9.0
pyobjc-framework-ScreenCaptureKit>=9.0
```

**Windows 依赖**

```
pywin32>=306
windows-capture>=0.3.0  # 或 d3dshot
keyboard>=0.13.0
```

### B. 参考资料

- [Apple ScreenCaptureKit Documentation](https://developer.apple.com/documentation/screencapturekit)
- [DXGI Desktop Duplication API](https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api)
- [Windows.Graphics.Capture](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture)
- `FUNCTION.md` — 现有色彩算法文档

***

> **策划人**：开发团队\
> **审核**：待定\
> **批准**：待定
