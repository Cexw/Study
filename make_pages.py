#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 GitHub Pages 生成根目录入口页：python make_pages.py

为什么需要这个脚本：
  GitHub Pages 要"从仓库根目录发布"时，访问 https://<用户>.github.io/Study/ 需要一个根目录的
  index.html。但我们的站点本体是 resource-lab/index.html，靠相对路径读 data/resources.js。
  直接把文件复制过去会因为路径不对而白屏，所以这里做两件事：
    1. 复制一份到根目录，并把 data/... 的引用改成 resource-lab/data/...
    2. 写一个 .nojekyll —— 否则 GitHub 会用 Jekyll 处理站点，以下划线开头的文件/目录
       会被静默忽略，而且根目录的 README.md 会被当成首页。

用脚本生成而不是手工维护，是为了避免"根目录那份和 resource-lab 那份慢慢不一致"。
resource-lab/index.html 始终是唯一的真源，这个脚本只做机械改写。

用法：
    python make_pages.py          # 生成 / 更新根目录 index.html 与 .nojekyll
    python make_pages.py --check  # 只检查是否已同步（CI/提交前用），不同步则退出码 1
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "resource-lab" / "index.html"
TARGET = HERE / "index.html"
NOJEKYLL = HERE / ".nojekyll"
BANNER = ("<!-- 自动生成，请勿手改：源文件是 resource-lab/index.html，"
          "由 make_pages.py 改写相对路径后生成 -->\n")

# 根目录这份要读子目录里的数据，所以只改这一个静态资源引用
REWRITES = [
    ('<script src="data/resources.js">', '<script src="resource-lab/data/resources.js">'),
    # 图标也要加前缀，否则根目录版会 404（浏览器控制台报 favicon 加载失败）
    ('href="icon.ico', 'href="resource-lab/icon.ico'),
    ('href="icon-192.png', 'href="resource-lab/icon-192.png'),
    ('href="apple-touch-icon.png', 'href="resource-lab/apple-touch-icon.png'),
]


def build(source_text: str) -> str:
    """把 resource-lab/index.html 的内容改写成适用于仓库根目录的版本。"""
    out = source_text
    for old, new in REWRITES:
        out = out.replace(old, new)
    return BANNER + out


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="make_pages.py",
                                 description="为 GitHub Pages 生成根目录入口页")
    ap.add_argument("--check", action="store_true", help="只检查是否已同步")
    args = ap.parse_args(argv)

    if not SOURCE.is_file():
        print("错误：找不到源文件 %s" % SOURCE, file=sys.stderr)
        return 2

    source_text = SOURCE.read_text(encoding="utf-8")
    want = build(source_text)

    # 校验改写真的生效了，否则说明源文件结构变了，这个脚本已经过时
    # 校验每条改写都真的命中了：源文件结构一变，这里会立刻报出来，而不是静默生成坏页面
    for old, new in REWRITES:
        if old in source_text and new not in want:
            print("错误：改写没有生效 —— %r\n      resource-lab/index.html 的结构可能变了，"
                  "请同步更新 make_pages.py 的 REWRITES" % old, file=sys.stderr)
            return 2
    if '<script src="resource-lab/data/resources.js">' not in want:
        print("错误：数据脚本改写没生效 —— 请检查 resource-lab/index.html 的引用方式",
              file=sys.stderr)
        return 2

    if args.check:
        same = TARGET.is_file() and TARGET.read_text(encoding="utf-8") == want
        nojekyll_ok = NOJEKYLL.is_file()
        print("根目录 index.html 已同步：%s" % ("是" if same else "否"))
        print(".nojekyll 存在：%s" % ("是" if nojekyll_ok else "否"))
        if not (same and nojekyll_ok):
            print("请运行 python make_pages.py 更新", file=sys.stderr)
            return 1
        return 0

    TARGET.write_text(want, encoding="utf-8")
    # .nojekyll 必须是空文件；内容会被 Pages 忽略，但保持空最省事
    if not NOJEKYLL.is_file():
        NOJEKYLL.write_text("", encoding="utf-8")

    print("已生成：")
    print("  %s（%.1f KB，源文件 %.1f KB）"
          % (TARGET, TARGET.stat().st_size / 1024, SOURCE.stat().st_size / 1024))
    print("  %s（禁用 Jekyll，避免下划线文件被忽略、README 被当首页）" % NOJEKYLL)
    print("\n相对路径改写：")
    for old, new in REWRITES:
        print("  %s\n    → %s" % (old, new))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
