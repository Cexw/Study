#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从源图生成真正的 favicon（icon.ico / PNG / apple-touch-icon）：python make_icon.py

背景：仓库里原来的 `icon.ico` 其实是一个 **JPEG 文件改了扩展名**（文件头是 ff d8 ff e0 JFIF），
      ICO 头（00 00 01 00）根本不存在。浏览器对 favicon 的格式比较宽容，多数情况能显示，
      但这属于"靠运气"：不同浏览器/场景（Safari、第三方解析器）可能直接忽略。

所以这里做两件事：
  1. 把源图转成**规范的多尺寸 ICO**（16/32/48/64/128/256，PNG 内嵌，Windows 与各浏览器都认）；
  2. 顺带导出 PNG 版本，供 `apple-touch-icon` 与 `manifest` 使用。

源图可以是 JPEG / PNG / WebP —— Pillow 会自动识别，扩展名不影响。

用法：
    python make_icon.py                    # 用 resource-lab/icon.ico 作为源图
    python make_icon.py --src 别的图.png
    python make_icon.py --check            # 只校验现有 icon.ico 是否为规范 ICO
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SRC = HERE / "icon.ico"           # 源图（历史原因就叫这个名字，其实是 JPEG）
ICO_OUT = HERE / "icon.ico"
PNG_OUT = HERE / "icon-192.png"
APPLE_OUT = HERE / "apple-touch-icon.png"

# ICO 里放的尺寸。Windows 任务栏/资源管理器用 16~48，高清屏与 PWA 用 128/256。
ICO_SIZES = (16, 32, 48, 64, 128, 256)
PNG_SIZE = 192
APPLE_SIZE = 180
ICO_MAGIC = b"\x00\x00\x01\x00"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def ico_kind(path: Path) -> str:
    """判断一个 .ico 文件到底是 ICO 还是别的格式（返回中文描述）。"""
    if not path.is_file():
        return "不存在"
    head = path.read_bytes()[:32]
    if head.startswith(ICO_MAGIC):
        return "ICO"
    if head.startswith(PNG_MAGIC):
        return "PNG（扩展名是 .ico）"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG（扩展名是 .ico）"
    if head.startswith(b"GIF8"):
        return "GIF（扩展名是 .ico）"
    if head.startswith(b"RIFF"):
        return "WebP（扩展名是 .ico）"
    return "未知格式"


def read_ico_entries(path: Path) -> list[tuple[int, int, int]]:
    """读 ICO 目录，返回 [(宽, 高, 字节数)]；不是 ICO 就返回空。"""
    b = path.read_bytes()
    if not b.startswith(ICO_MAGIC):
        return []
    _res, _typ, count = struct.unpack_from("<HHH", b, 0)
    out = []
    for i in range(count):
        off = 6 + i * 16
        if off + 16 > len(b):
            break
        w, h, _c, _r, _p, _bpp, size, dataoff = struct.unpack_from("<BBBBHHII", b, off)
        out.append((w or 256, h or 256, size))
    return out


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="make_icon.py", description="生成规范的 favicon")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="源图（JPEG/PNG/WebP 都行）")
    ap.add_argument("--check", action="store_true", help="只校验，不生成")
    args = ap.parse_args(argv)

    src = Path(args.src)

    if args.check:
        kind = ico_kind(ICO_OUT)
        print("icon.ico 实际格式：%s" % kind)
        entries = read_ico_entries(ICO_OUT)
        if entries:
            print("内含 %d 个尺寸：%s" % (len(entries), ", ".join("%dx%d" % (w, h) for w, h, _ in entries)))
        ok = kind == "ICO" and entries
        if not ok:
            print("✗ 不是规范 ICO，请运行 python make_icon.py", file=sys.stderr)
            return 1
        print("✓ 是规范的多尺寸 ICO")
        return 0

    if not src.is_file():
        print("错误：找不到源图 %s" % src, file=sys.stderr)
        return 2

    try:
        from PIL import Image
    except ImportError:
        print("错误：需要 Pillow（pip install Pillow）", file=sys.stderr)
        return 2

    im = Image.open(src)
    print("源图：%s" % src.name)
    print("  实际格式：%s   尺寸：%dx%d   模式：%s" % (im.format, im.width, im.height, im.mode))
    if im.format == "JPEG" and src.suffix.lower() == ".ico":
        print("  ! 注意：这个 .ico 其实是 JPEG，本次会把它转成真正的 ICO")

    # JPEG 没有 alpha，统一转 RGBA 以便生成带透明通道的图标
    im = im.convert("RGBA")

    # 源图非正方形时先裁成正方形（取中心），避免生成时被拉伸变形
    if im.width != im.height:
        side = min(im.width, im.height)
        left = (im.width - side) // 2
        top = (im.height - side) // 2
        im = im.crop((left, top, left + side, top + side))
        print("  已居中裁剪为正方形：%dx%d" % (im.width, im.height))

    # ICO 内的每张图单独缩放；sizes= 参数会自己生成多尺寸
    ico_sizes = [(s, s) for s in ICO_SIZES if s <= max(im.width, 256) or s <= 256]
    ico_sizes = [(s, s) for s in ICO_SIZES]
    im.save(ICO_OUT, format="ICO", sizes=ico_sizes)
    print("\n已生成：")
    print("  %s（%d 字节，%d 个尺寸）"
          % (ICO_OUT, ICO_OUT.stat().st_size, len(read_ico_entries(ICO_OUT))))

    im.resize((PNG_SIZE, PNG_SIZE), Image.LANCZOS).save(PNG_OUT, format="PNG", optimize=True)
    print("  %s（%d 字节）" % (PNG_OUT, PNG_OUT.stat().st_size))

    im.resize((APPLE_SIZE, APPLE_SIZE), Image.LANCZOS).save(APPLE_OUT, format="PNG", optimize=True)
    print("  %s（%d 字节）" % (APPLE_OUT, APPLE_OUT.stat().st_size))

    entries = read_ico_entries(ICO_OUT)
    print("\n校验：icon.ico 格式=%s，尺寸=%s"
          % (ico_kind(ICO_OUT), ", ".join("%dx%d" % (w, h) for w, h, _ in entries)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
