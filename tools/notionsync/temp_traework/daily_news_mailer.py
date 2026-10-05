#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import base64
import html
import json
import os
import re
import smtplib
import subprocess
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


DEFAULT_RECIPIENTS = [
    "xiaoli.zhou.ch@icloud.com",
    "xiaoli.zhou.ch@qq.com",
]


def parse_args():
    parser = argparse.ArgumentParser(description="将每日资讯 Markdown 渲染为 HTML 邮件并发送。")
    parser.add_argument("--input", help="输入的资讯 Markdown 文件路径。")
    parser.add_argument("--latest-dir", help="自动从目录中挑选最新的 Markdown 文件。")
    parser.add_argument("--topic-filter", help="按主题名或 Topic 文本过滤最新文件。")
    parser.add_argument(
        "--recipients",
        nargs="*",
        default=DEFAULT_RECIPIENTS,
        help="收件人列表；默认使用整理要求中的两个收件人。",
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent / "outbox"),
        help="渲染后的 HTML 输出目录。",
    )
    parser.add_argument(
        "--method",
        choices=["auto", "outlook", "smtp"],
        default="auto",
        help="发送方式；默认自动优先 Outlook，再回退 SMTP。",
    )
    parser.add_argument("--send", action="store_true", help="实际发送邮件。")
    parser.add_argument("--subject-prefix", default="[每日资讯/Daily Brief]", help="邮件主题前缀。")
    return parser.parse_args()


def resolve_input_path(args):
    if args.input:
        path = Path(args.input).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"输入文件不存在：{path}")
        return path
    if not args.latest_dir:
        raise ValueError("需要提供 `--input` 或 `--latest-dir`。")
    latest_dir = Path(args.latest_dir).expanduser().resolve()
    if not latest_dir.exists():
        raise FileNotFoundError(f"目录不存在：{latest_dir}")
    candidates = sorted(latest_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if args.topic_filter:
        topic_filter = args.topic_filter.strip().lower()
        filtered = []
        for item in candidates:
            name_hit = topic_filter in item.name.lower()
            if name_hit:
                filtered.append(item)
                continue
            try:
                body = item.read_text(encoding="utf-8-sig", errors="ignore").lower()
            except OSError:
                continue
            if topic_filter in body:
                filtered.append(item)
        candidates = filtered
    if not candidates:
        raise FileNotFoundError("没有找到符合条件的 Markdown 资讯文件。")
    return candidates[0]


def split_sections(lines):
    sections = {}
    current_h2 = None
    current_h3 = None
    for line in lines:
        if line.startswith("## "):
            current_h2 = line[3:].strip()
            current_h3 = None
            sections.setdefault(current_h2, {"content": [], "subsections": {}})
            continue
        if line.startswith("### ") and current_h2:
            current_h3 = line[4:].strip()
            sections[current_h2]["subsections"].setdefault(current_h3, [])
            continue
        if current_h2:
            if current_h3:
                sections[current_h2]["subsections"][current_h3].append(line)
            else:
                sections[current_h2]["content"].append(line)
    return sections


def extract_title_meta(first_line):
    raw_title = re.sub(r"^#\s*", "", first_line).strip()
    display = raw_title
    if "】" in display:
        display = display.split("】", 1)[1].strip()
    parts = [part.strip() for part in display.split("｜")]
    brief_name = parts[0] if parts else display
    datetime_text = parts[1] if len(parts) > 1 else ""
    date_text = datetime_text.split()[0] if datetime_text else ""
    return {
        "raw_title": raw_title,
        "brief_name": brief_name,
        "datetime_text": datetime_text,
        "date_text": date_text,
    }


def extract_topic(lines):
    for line in lines:
        match = re.match(r"Topic:\s*`(.+?)`", line.strip())
        if match:
            return match.group(1)
    return ""


def extract_time_window(lines):
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            stripped = stripped[1:].strip()
        if "时间窗口：" in stripped and "—" in stripped:
            _, window = stripped.split("时间窗口：", 1)
            start, end = window.split("—", 1)
            return {
                "start": start.replace("`", "").strip(),
                "end": end.replace("`", "").strip(),
            }
    return {"start": "", "end": ""}


def inline_markdown_to_html(text):
    escaped = html.escape(text, quote=True)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}" style="color:#1a5c9a;text-decoration:none;">{m.group(1)}</a>',
        escaped,
    )
    return escaped


def compact_lines(lines):
    return [line.rstrip() for line in lines if line.strip()]


def parse_markdown_table(lines):
    compact = compact_lines(lines)
    if len(compact) < 2:
        return None
    if not all(line.strip().startswith("|") and line.strip().endswith("|") for line in compact[:2]):
        return None
    rows = []
    for line in compact:
        cols = [cell.strip() for cell in line.strip()[1:-1].split("|")]
        rows.append(cols)
    if not rows or not all(re.fullmatch(r"[:\-\s]+", cell) for cell in rows[1]):
        return None
    header = rows[0]
    body = rows[2:]
    return {"header": header, "body": body}


def lines_to_paragraphs(lines):
    blocks = []
    current = []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            if current:
                blocks.append(" ".join(part.strip() for part in current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(" ".join(part.strip() for part in current))
    return blocks


def render_notice_block(paragraphs):
    if not paragraphs:
        return ""
    body = "".join(
        f'<div style="font-size:14px;line-height:1.8;margin-top:6px;">{inline_markdown_to_html(item)}</div>'
        for item in paragraphs
    )
    return f"""
    <tr>
      <td style="padding:12px 32px 22px;">
        <div style="border-bottom:1px solid #e0dbcf;padding-bottom:16px;margin-bottom:16px;">
          {body}
        </div>
      </td>
    </tr>
    """


def render_category_sections(subsections):
    parts = []
    for title, lines in subsections.items():
        bullets = [line[2:].strip() for line in compact_lines(lines) if line.lstrip().startswith("- ")]
        paragraphs = [line for line in compact_lines(lines) if not line.lstrip().startswith("- ")]
        bullet_html = "".join(
            f'<div style="font-size:14px;line-height:1.8;margin-bottom:8px;">• {inline_markdown_to_html(item)}</div>'
            for item in bullets
        )
        paragraph_html = "".join(
            f'<div style="font-size:14px;line-height:1.8;margin-bottom:8px;">{inline_markdown_to_html(item)}</div>'
            for item in paragraphs
        )
        parts.append(
            f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <div style="font-size:16px;font-weight:bold;border-bottom:2px solid #1a1a1a;padding-bottom:6px;margin-bottom:12px;">{inline_markdown_to_html(title)}</div>
                {bullet_html or paragraph_html or '<div style="font-size:14px;color:#666;">暂无内容。</div>'}
                {paragraph_html if bullet_html else ''}
              </td>
            </tr>
            """
        )
    return "".join(parts)


def render_simple_section(title, lines):
    filtered = compact_lines(lines)
    if not filtered:
        return ""
    bullets = [line[2:].strip() for line in filtered if line.lstrip().startswith("- ")]
    paragraphs = [line for line in filtered if not line.lstrip().startswith("- ")]
    inner = "".join(
        f'<div style="font-size:14px;line-height:1.8;margin-bottom:8px;">• {inline_markdown_to_html(item)}</div>'
        for item in bullets
    ) + "".join(
        f'<div style="font-size:14px;line-height:1.8;margin-bottom:8px;">{inline_markdown_to_html(item)}</div>'
        for item in paragraphs
    )
    return f"""
    <tr>
      <td style="padding:0 32px 22px;">
        <div style="font-size:16px;font-weight:bold;border-bottom:2px solid #1a1a1a;padding-bottom:6px;margin-bottom:12px;">{inline_markdown_to_html(title)}</div>
        {inner}
      </td>
    </tr>
    """


def render_table_section(title, table):
    if not table:
        return ""
    head = "".join(
        f'<th style="border:1px solid #d8d2c4;background:#f3efe7;padding:8px 10px;font-size:12px;text-align:left;">{inline_markdown_to_html(col)}</th>'
        for col in table["header"]
    )
    body_rows = []
    for row in table["body"][:12]:
        cells = "".join(
            f'<td style="border:1px solid #e3ddd0;padding:8px 10px;font-size:12px;line-height:1.6;vertical-align:top;">{inline_markdown_to_html(cell)}</td>'
            for cell in row
        )
        body_rows.append(f"<tr>{cells}</tr>")
    return f"""
    <tr>
      <td style="padding:0 32px 22px;">
        <div style="font-size:16px;font-weight:bold;border-bottom:2px solid #1a1a1a;padding-bottom:6px;margin-bottom:12px;">{inline_markdown_to_html(title)}</div>
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
          <thead><tr>{head}</tr></thead>
          <tbody>{''.join(body_rows)}</tbody>
        </table>
      </td>
    </tr>
    """


def count_top_stories(lines):
    bullets = [line for line in compact_lines(lines) if line.lstrip().startswith("- ")]
    if bullets:
        return len(bullets)
    joined = " ".join(compact_lines(lines))
    if "无重大更新" in joined.lower() or "no major updates" in joined.lower():
        return 0
    paragraphs = lines_to_paragraphs(lines)
    return len(paragraphs)


def count_routine_items(subsections):
    count = 0
    for _, lines in subsections.items():
        bullets = [line for line in compact_lines(lines) if line.lstrip().startswith("- ")]
        valid = [item for item in bullets if "暂无" not in item and "No qualifying" not in item]
        count += len(valid)
    return count


def build_subject(meta, top_story_count, total_count, prefix):
    date_text = meta["date_text"] or time.strftime("%Y-%m-%d")
    return f"{prefix} {meta['brief_name']}｜{date_text}｜P0 {top_story_count}条｜共{total_count}条"


def build_html(parsed, subject):
    top_story_paragraphs = lines_to_paragraphs(parsed["top_story_lines"])
    category_html = render_category_sections(parsed["sections"].get("分类栏目 / Sections", {}).get("subsections", {}))
    notes_html = render_simple_section("说明 / Notes", parsed["sections"].get("说明 / Notes", {}).get("content", []))
    actions_html = render_simple_section("行动建议 / Actions", parsed["sections"].get("行动建议 / Actions", {}).get("content", []))
    audit_table = parse_markdown_table(parsed["sections"].get("核查记录 / Audit Trail", {}).get("content", []))
    audit_html = render_table_section("核查记录 / Audit Trail", audit_table)
    footer_window = parsed["time_window"]["start"] and parsed["time_window"]["end"]
    footer_text = (
        f"本邮件由 TRAE WORK 自动生成｜时间窗口：{parsed['time_window']['start']}—{parsed['time_window']['end']}"
        if footer_window
        else "本邮件由 TRAE WORK 自动生成。"
    )
    report_date = parsed["meta"]["datetime_text"] or parsed["meta"]["date_text"] or time.strftime("%Y-%m-%d")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>{html.escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f0;font-family:Georgia,'Times New Roman',serif;color:#1a1a1a;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f5f5f0;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table width="720" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;border:1px solid #d8d2c4;">
          <tr>
            <td style="padding:28px 32px 18px;border-bottom:3px double #1a1a1a;">
              <div style="font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#8a7f6b;">Daily Intelligence Brief</div>
              <div style="font-size:32px;font-weight:bold;line-height:1.2;margin-top:6px;">每日资讯</div>
              <div style="font-size:13px;color:#666;margin-top:8px;">{inline_markdown_to_html(report_date)}｜{inline_markdown_to_html(parsed["meta"]["brief_name"])}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:22px 32px 8px;">
              <div style="font-size:18px;font-weight:bold;border-left:5px solid #b33a2b;padding-left:10px;">今日必看 / Top Stories</div>
            </td>
          </tr>
          {render_notice_block(top_story_paragraphs)}
          {category_html}
          {audit_html}
          {notes_html}
          {actions_html}
          <tr>
            <td style="padding:18px 32px 28px;border-top:1px solid #d8d2c4;font-size:12px;color:#888;">
              {inline_markdown_to_html(footer_text)}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def save_html(html_text, input_path, out_dir):
    output_dir = Path(out_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = input_path.stem + ".html"
    output_path = output_dir / filename
    output_path.write_text(html_text, encoding="utf-8")
    return output_path


def detect_send_method(preferred):
    if preferred in {"outlook", "smtp"}:
        return preferred
    if sys.platform.startswith("win"):
        return "outlook"
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD"]
    if all(os.environ.get(name) for name in required):
        return "smtp"
    raise RuntimeError("未检测到可用发送方式：Outlook 可优先使用，否则请配置 SMTP_* 环境变量。")


def send_via_outlook(subject, html_path, recipients):
    ps_script = r"""
$ErrorActionPreference = "Stop"
$subject = $env:TRAE_MAIL_SUBJECT
$htmlPath = $env:TRAE_MAIL_HTML
$recipients = $env:TRAE_MAIL_RECIPIENTS
$outlook = $null
for ($i = 0; $i -lt 5; $i++) {
  try {
    $outlook = [System.Runtime.InteropServices.Marshal]::GetActiveObject("Outlook.Application")
    if ($outlook -ne $null) { break }
  } catch {}
  try {
    $outlook = New-Object -ComObject Outlook.Application
    if ($outlook -ne $null) { break }
  } catch {
    if ($i -eq 4) { throw }
    Start-Sleep -Seconds 2
  }
}
if ($outlook -eq $null) {
  throw "Unable to connect to Outlook.Application."
}
$mail = $outlook.CreateItem(0)
$mail.Subject = $subject
$mail.To = $recipients
$mail.HTMLBody = Get-Content -Raw -Encoding UTF8 $htmlPath
$mail.Send()
Write-Output "SENT_OK"
"""
    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    env = os.environ.copy()
    env["TRAE_MAIL_SUBJECT"] = subject
    env["TRAE_MAIL_HTML"] = str(html_path)
    env["TRAE_MAIL_RECIPIENTS"] = "; ".join(recipients)
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-EncodedCommand",
        encoded,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or "Outlook 发送失败。")
    return (proc.stdout or "").strip() or "SENT_OK"


def send_via_smtp(subject, html_text, recipients):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ.get("NEWS_MAIL_FROM", username)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_text, "html", "utf-8"))
    with smtplib.SMTP_SSL(host, port) as server:
        server.login(username, password)
        server.sendmail(sender, recipients, msg.as_string())
    return "SENT_OK"


def parse_document(path):
    text = path.read_text(encoding="utf-8-sig")
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or not lines[0].startswith("# "):
        raise ValueError("Markdown 第一行必须是一级标题。")
    sections = split_sections(lines)
    top_story_lines = sections.get("今日必看 / Top Stories", {}).get("content", [])
    meta = extract_title_meta(lines[0])
    topic = extract_topic(lines)
    time_window = extract_time_window(lines)
    top_story_count = count_top_stories(top_story_lines)
    total_count = top_story_count + count_routine_items(sections.get("分类栏目 / Sections", {}).get("subsections", {}))
    return {
        "path": str(path),
        "meta": meta,
        "topic": topic,
        "time_window": time_window,
        "sections": sections,
        "top_story_lines": top_story_lines,
        "top_story_count": top_story_count,
        "total_count": total_count,
    }


def main():
    args = parse_args()
    input_path = resolve_input_path(args)
    parsed = parse_document(input_path)
    subject = build_subject(parsed["meta"], parsed["top_story_count"], parsed["total_count"], args.subject_prefix)
    html_text = build_html(parsed, subject)
    html_path = save_html(html_text, input_path, args.out_dir)

    send_result = "dry_run"
    method = None
    if args.send:
        method = detect_send_method(args.method)
        if method == "outlook":
            send_result = send_via_outlook(subject, html_path, args.recipients)
        else:
            send_result = send_via_smtp(subject, html_text, args.recipients)

    print(
        json.dumps(
            {
                "ok": True,
                "input": str(input_path),
                "html_output": str(html_path),
                "topic": parsed["topic"],
                "subject": subject,
                "top_story_count": parsed["top_story_count"],
                "total_count": parsed["total_count"],
                "recipients": args.recipients,
                "send_method": method,
                "send_result": send_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise
