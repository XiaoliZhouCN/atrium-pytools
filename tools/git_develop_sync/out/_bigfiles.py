"""列出 AtriumNote 提交里的大文件（git ls-tree 尺寸），供决定如何处理。"""
import subprocess
from pathlib import Path

REPO = r'D:\Repositories\Manager\AtriumNote'
done = subprocess.run(['git', '-C', REPO, 'ls-tree', '-r', '-l', 'HEAD'],
                      capture_output=True, text=True, encoding='utf-8', errors='replace')
rows = []
for line in done.stdout.splitlines():
    parts = line.split('\t')
    if len(parts) != 2:
        continue
    meta = parts[0].split()
    size = int(meta[3])
    if size > 40 * 1024 * 1024:
        rows.append((size, parts[1]))

rows.sort(reverse=True)
print(f'HEAD 里 >40MB 的文件 {len(rows)} 个：')
for size, path in rows:
    print(f'  {size / 1048576:8.2f} MB  {path}')

print('\n其中 >100MB（GitHub 硬上限，必然推不上去）：')
for size, path in rows:
    if size > 100 * 1024 * 1024:
        print(f'  {size / 1048576:8.2f} MB  {path}')
