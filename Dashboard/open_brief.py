#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
open_brief.py — 把最新一份 daily_brief 產出的 Markdown 轉成網頁並開啟

daily_brief.py 產出的是 .md，Windows 常常沒有預設開啟程式。
這支把它包成 HTML（用 marked.js 渲染，表格也正常）後用瀏覽器開啟。

用法:
    python open_brief.py                # 開啟 briefs/ 裡最新的一份
    python open_brief.py --dir ./briefs
    python open_brief.py --no-open      # 只產生 HTML 不開瀏覽器
"""

import argparse
import glob
import json
import os
import sys
import webbrowser

_HERE = os.path.dirname(os.path.abspath(__file__))

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+TC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#EDF0F3; --sheet:#FFFFFF; --ink:#15202B; --ink-2:#5A6B7C;
  --rule:#C9D3DC; --grid:rgba(21,32,43,.045);
  --up:#1B6B4A; --down:#A5301E; --warn:#B0771A; --line:#2B4C6F;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Noto Sans TC',system-ui,sans-serif;
  background:var(--paper);color:var(--ink);line-height:1.7;
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),
                   linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:28px 28px;
  padding:32px 20px 80px;
}
.wrap{max-width:860px;margin:0 auto;background:var(--sheet);
  border:1px solid var(--rule);padding:44px 52px 56px}
.meta{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-2);padding-bottom:14px;
  border-bottom:2px solid var(--ink);margin-bottom:28px;
  display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
h1{font-size:27px;font-weight:700;letter-spacing:-.01em;margin:34px 0 14px;line-height:1.25}
h1:first-of-type{margin-top:0}
h2{font-size:20px;font-weight:600;margin:32px 0 12px;padding-bottom:7px;
  border-bottom:1px solid var(--rule)}
h3{font-size:16px;font-weight:600;margin:24px 0 9px}
p{margin:12px 0}
ul,ol{margin:12px 0 12px 24px}
li{margin:5px 0}
strong{font-weight:600}
code{font-family:'IBM Plex Mono',monospace;font-size:.9em;
  background:#EDF1F4;padding:1px 5px;border-radius:3px}
pre{background:#F7F9FB;border:1px solid var(--rule);padding:14px 16px;
  overflow-x:auto;margin:14px 0}
pre code{background:none;padding:0}
table{width:100%;border-collapse:collapse;margin:18px 0;font-size:14px}
th{background:#F2F5F8;text-align:left;padding:9px 12px;
  border-bottom:2px solid var(--rule);font-weight:600;font-size:12.5px}
td{padding:9px 12px;border-bottom:1px solid #EDF1F4}
td:nth-child(n+3){font-family:'IBM Plex Mono',monospace;font-variant-numeric:tabular-nums}
tr:hover td{background:#F7F9FB}
blockquote{border-left:3px solid var(--line);padding:4px 0 4px 16px;
  margin:16px 0;color:var(--ink-2)}
hr{border:0;border-top:1px solid var(--rule);margin:30px 0}
a{color:var(--line)}
@media(max-width:640px){.wrap{padding:26px 20px 34px}body{padding:16px 10px 40px}}
</style>
</head><body>
<div class="wrap">
  <div class="meta"><span>__FILE__</span><span>每日市場初判</span></div>
  <div id="content">載入中…</div>
</div>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>
const md = __MD__;
document.getElementById('content').innerHTML =
  (window.marked ? marked.parse(md)
                 : '<pre>' + md.replace(/[&<>]/g, c =>
                     ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) + '</pre>');
</script>
</body></html>"""


def find_latest(dirpath):
    files = glob.glob(os.path.join(dirpath, "*.md"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def render(md_path, out_path):
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    name = os.path.basename(md_path)
    html = (TEMPLATE
            .replace("__MD__", json.dumps(md))
            .replace("__TITLE__", name.replace(".md", ""))
            .replace("__FILE__", name))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    p = argparse.ArgumentParser(description="開啟最新的每日初判")
    p.add_argument("--dir", default=os.path.join(_HERE, "briefs"),
                   help="brief 資料夾（預設 ./briefs）")
    p.add_argument("--no-open", action="store_true", help="只產生 HTML 不開瀏覽器")
    args = p.parse_args()

    if not os.path.isdir(args.dir):
        sys.exit(f"找不到資料夾：{args.dir}")

    latest = find_latest(args.dir)
    if not latest:
        sys.exit(f"{args.dir} 裡沒有任何 .md 報告")

    out = os.path.join(args.dir, "brief_latest.html")
    render(latest, out)
    print(f"  報告：{os.path.basename(latest)}")
    if not args.no_open:
        webbrowser.open("file:///" + os.path.abspath(out).replace("\\", "/"))
        print("  已在瀏覽器開啟")


if __name__ == "__main__":
    main()
