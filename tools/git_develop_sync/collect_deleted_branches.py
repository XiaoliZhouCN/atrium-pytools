"""汇总本轮删除的分支（从各次运行日志里提取），生成可追溯记录。"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

OUT = Path(r'D:\Repositories\Manager\AtriumPyTools\tools\git_develop_sync\out')
TEMP = Path(os.environ.get('TEMP', r'D:\System\TempFiles\Temp'))
LOGS = [OUT / 'consolidate_apply.log']
LOGS += sorted(TEMP.glob('cons_*.log'))
LOGS += sorted(TEMP.glob('consolidate_*.log'))

def read_log(path: Path) -> str:
    """PowerShell 的 Tee-Object 写的是 UTF-16LE（带 BOM），这里做兼容。"""
    raw = path.read_bytes()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16', errors='replace')
    return raw.decode('utf-8', errors='replace')


records: dict[str, dict] = {}
current = None
for log in LOGS:
    if not log.is_file():
        continue
    for line in read_log(log).splitlines():
        m = re.match(r'^>>> (\S+)\s+\((.+)\)', line.strip())
        if m:
            current = m.group(1)
            records.setdefault(current, {'repo': m.group(2), 'deleted_local': [],
                                         'deleted_remote': [], 'merged': [], 'logs': []})
            records[current]['logs'].append(log.name)
            continue
        if current is None:
            continue
        text = line.strip()
        if text.startswith('删除本地分支 '):
            name = text.split('删除本地分支 ')[1].split('（')[0].strip()
            records[current]['deleted_local'].append(name)
        elif text.startswith('删除远端分支 '):
            name = text.split('删除远端分支 ')[1].strip()
            records[current]['deleted_remote'].append(name)
        elif text.startswith('删除远端孤儿分支 '):
            name = text.split('删除远端孤儿分支 ')[1].split('（')[0].strip()
            records[current]['deleted_remote'].append(name)
        elif text.startswith('合并 ') and ' -> develop' in text:
            records[current]['merged'].append(text.split(' -> develop')[0][3:].strip())

for item in records.values():
    item['deleted_local'] = sorted(set(item['deleted_local']))
    item['deleted_remote'] = sorted(set(item['deleted_remote']))
    item['merged'] = sorted(set(item['merged']))
    item['logs'] = sorted(set(item['logs']))

payload = {'time': datetime.now().astimezone().isoformat(timespec='seconds'),
           'note': '被删除分支的内容均已确认并入 develop；本地可用 git reflog 追溯，'
                   '远端分支的提交也都能在 develop 历史里找到',
           'repos': records}
(OUT / 'git_deleted_branches.json').write_text(
    json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

total_l = total_r = 0
for name, item in records.items():
    if not (item['deleted_local'] or item['deleted_remote'] or item['merged']):
        continue
    total_l += len(item['deleted_local'])
    total_r += len(item['deleted_remote'])
    print(f"{name}: 合并 {item['merged'] or '无'} | 删本地 {item['deleted_local'] or '无'} "
          f"| 删远端 {item['deleted_remote'] or '无'}")
print(f'\n合计：删除本地分支 {total_l} 个，远端分支 {total_r} 个')
print('记录:', OUT / 'git_deleted_branches.json')
