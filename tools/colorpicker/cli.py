"""Colorpicker 命令行工具入口（供测试/调试使用）"""

import argparse
from colorpicker import sample_at_cursor, sample_at

def main():
    parser = argparse.ArgumentParser(description="获取屏幕像素颜色值")
    parser.add_argument("--x", type=int, help="屏幕 x 坐标 (像素)")
    parser.add_argument("--y", type=int, help="屏幕 y 坐标 (像素)")
    parser.add_argument(
        "--cursor", action="store_true",
        help="跟随鼠标位置（若未指定 x/y，则默认启用）"
    )
    parser.add_argument("--digits", type=int, default=6, help="输出小数位数")
    args = parser.parse_args()

    # 如果未提供坐标，自动启用 cursor 模式
    if args.cursor or (args.x is None or args.y is None):
        result = sample_at_cursor()
    else:
        result = sample_at(args.x, args.y)

    fmt = f"{{:.{args.digits}f}}"
    print(f"R {fmt.format(result['r'])}")
    print(f"G {fmt.format(result['g'])}")
    print(f"B {fmt.format(result['b'])}")
    print(f"X {result['x']:.1f} Y {result['y']:.1f}")

if __name__ == "__main__":
    main()