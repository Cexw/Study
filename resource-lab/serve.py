#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学习资源库 · 本地服务（纯 Python 标准库，无第三方依赖）。

在网页里输入 B 站 UP 主的名称 / ID / 主页链接，后端自动解析 → 抓合集 →
自动识别学科 → 整理 → 落盘，并把最新的 resources.json / resources.js 返回给前端。

复用的现成能力（**这里不重写任何爬取逻辑**）：
  · crawl_bilibili.resolve_up / Crawler / collect_by_search / build_output
  · build_resources.detect_subject / build_one_source / build / write_outputs

接口一览：
  GET  /api/ping          探测"是不是跑在 serve.py 上"
  POST /api/crawl         提交抓取任务（立刻返回，后台线程干活）
  GET  /api/job/<id>      查询任务状态 / 日志 / 进度 / 结果
  GET  /api/sources       当前来源清单（读 data/resources.json）
  POST /api/reset         删掉用户添加的 raw 文件并重建数据
  GET  /api/subjects      学科清单（内置 + 自定义，含计数）
  POST /api/subjects      新增自定义学科 {"name": "...", "id": "可选"}
  DELETE /api/subjects/<id>  删除自定义学科（POST /api/subjects/delete 等价）
  POST /api/source/subject   给来源改学科 {"id": "...", "subject_id": "..."}
  POST /api/source/delete    删除已导入的 UP 主（raw 软删除进 data/trash/）
  GET  /...               静态文件（index.html / data/* / screenshots/* 等）

测试注入口：Server(crawl_fn=...) 可以替换"解析 + 抓取"这一步（见 test_serve.py），
这样离线测试跑的是真实 HTTP，但完全不联网。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_resources as B  # noqa: E402
import crawl_bilibili as C  # noqa: E402


def _setup_console() -> None:
    """Windows 控制台默认 GBK，中文输出会炸；统一改成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # 流被重定向/不支持 reconfigure 时安静跳过
            pass


_setup_console()

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

SCHEMA_VERSION = 1
# 兼容旧调用：/api/crawl 的 subject 校验已经升级成"auto + 任意已知学科 id"，
# 这个常量只保留给老代码/文档引用，不再参与校验。
SUPPORTED_SUBJECTS = ("auto", "math", "physics", "other")

# 自定义学科 id 允许的字符：只用 ascii 小写/数字/下划线/连字符，避免路径与 URL 里出幺蛾子
SUBJECT_ID_RE = re.compile(r"^[a-z0-9_-]+$")
CUSTOM_SUBJECT_PREFIX = "custom-"
TRASH_DIRNAME = "trash"
# 软删除文件的时间戳后缀格式：20261004T010203（和 spec 里的示例一致）
TRASH_STAMP_FMT = "%Y%m%dT%H%M%S"

MAX_LOG_LINES = 2000  # 单任务日志上限，防止极端 UP 主把内存撑爆（保留最后 N 行）

HOST_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 8765
PORT_TRIES = 10          # 端口被占用时往后试几个
SLEEP_DEFAULT = 0.6
MAX_VIDEOS_DEFAULT = 3000

# 内置来源用固定的 raw 文件名：
# 一数的展示 id 是 "yishu"，但历史文件叫 raw_bilibili.json，必须沿用它，
# 否则同一个 mid 会同时存在 raw_bilibili.json 和 raw_yishu.json 两份，
# build() 会把同一个来源算两遍（来源重复、视频数翻倍）。
LEGACY_RAW_FILES = {14229967: "raw_bilibili.json", 23630128: "raw_huangfuren.json"}
PRESET_RAW_FILES = frozenset(LEGACY_RAW_FILES.values())

# 爬虫进度行形如： "  [3/7] 高中数学基础与解法全集 —— 188/188"
PROGRESS_LINE_RE = re.compile(r"\[(\d+)/(\d+)\][^\n]*?——\s*(\d+)/(\d+)")
PROGRESS_TOTAL_RE = re.compile(r"累计\s*(\d+)")

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}
DEFAULT_MIME = "application/octet-stream"

NOT_FOUND_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>404 · 没有找到</title>
<style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0f1115;color:#e8eaf0;
margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}
.box{max-width:520px;padding:32px;line-height:1.8}
h1{font-size:20px;margin:0 0 12px}
p{color:#9aa3b2;margin:6px 0}
code{background:#1b1f27;padding:2px 6px;border-radius:4px;color:#c9d3e3}
a{color:#6aa9ff}
</style></head><body><div class="box">
<h1>404 · 没有找到这个文件</h1>
<p>地址可能写错了，或者文件已经被删掉。</p>
<p>回到 <a href="/">学习资源库首页</a></p>
</div></body></html>
"""

SERVER_ERROR_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>500 · 服务出错了</title></head>
<body style="font-family:system-ui,'Microsoft YaHei',sans-serif;background:#0f1115;color:#e8eaf0;padding:32px">
<h1 style="font-size:20px">500 · 服务内部出错了</h1>
<p style="color:#9aa3b2">详细信息请看启动服务时的终端输出。回到 <a href="/" style="color:#6aa9ff">首页</a>。</p>
</body></html>
"""


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------


def slug_for(mid: int) -> str:
    """来源 id / raw 文件名用的 slug：已知来源用 SOURCES 里的 id，否则 up<mid>。"""
    known = B.SOURCES.get(int(mid))
    raw = known["id"] if known else ("up%d" % int(mid))
    safe = re.sub(r"[^a-z0-9_]", "_", str(raw).lower())
    return safe or ("up%d" % int(mid))


def cap_videos(collections: list, limit: int) -> tuple[list, list]:
    """按"单次任务最多抓多少视频"截断，返回 (新的合集列表, 被截断的合集标题)。

    Crawler 只有"每个合集最多多少集"，没有全局上限；超大 UP 主必须在这里兜住，
    否则一次任务能抓几万条，前端和内存都吃不消。
    """
    if limit <= 0:
        return collections, []
    total = sum(len(c.get("videos") or []) for c in (collections or []))
    if total <= limit:
        return collections, []
    kept: list = []
    cut: list = []
    remaining = limit
    for collection in collections or []:
        videos = list(collection.get("videos") or [])
        title = collection.get("title") or "未命名合集"
        if remaining <= 0 or not videos:
            if videos:
                cut.append(title)
            continue
        if len(videos) > remaining:
            trimmed = dict(collection)
            trimmed["videos"] = videos[:remaining]
            trimmed["fetched"] = remaining
            trimmed["truncated"] = True
            kept.append(trimmed)
            cut.append(title)
            remaining = 0
        else:
            kept.append(collection)
            remaining -= len(videos)
    return kept, cut


def write_json_atomic(path: Path, data) -> None:
    """先写同目录临时文件再原子替换，避免进程被杀/并发写时留下半截 JSON。

    Windows 上 os.replace 可以直接覆盖已存在的目标文件，所以不需要先删旧文件。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class RebuildError(RuntimeError):
    """重建 resources.json 失败。新接口要把它翻译成 500 + 人话错误，而不是静默成功。"""


class HttpError(Exception):
    """带状态码的业务错误：由 Handler 统一翻译成 {"ok":false,"error":...}。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = int(status)
        self.message = message


# --------------------------------------------------------------------------
# 任务对象
# --------------------------------------------------------------------------


class Job:
    """一次抓取任务的内存状态：状态、阶段、日志、进度、结果、错误。

    只在内存里活着（服务重启即丢），持久化的东西都在 data/ 下。
    """

    def __init__(self, job_id: str, query: str, subject: str, name: str | None):
        self.id = job_id
        self.query = query
        self.subject = subject
        self.name = name
        self.started = time.time()
        self.state = "running"
        self.stage = "解析 UP 主"
        self.lock = threading.Lock()
        self._log: list[str] = []
        self._progress = {"collections": 0, "videos": 0}
        self._result = None
        self._error = None

    # ---- 写入（全部加锁） ----
    def add_log(self, *parts) -> None:
        line = " ".join(str(p) for p in parts).rstrip()
        if not line:
            return
        with self.lock:
            self._log.append(line)
            if len(self._log) > MAX_LOG_LINES:
                del self._log[:len(self._log) - MAX_LOG_LINES]

    def set_stage(self, stage: str) -> None:
        with self.lock:
            self.stage = stage

    def update_progress(self, collections=None, videos=None, videos_delta: int = 0) -> None:
        with self.lock:
            p = self._progress
            if collections is not None:
                p["collections"] = max(int(collections), p["collections"])
            if videos is not None:
                p["videos"] = max(int(videos), p["videos"])
            if videos_delta:
                p["videos"] = max(0, p["videos"] + int(videos_delta))

    def finish(self, result: dict, collections: int, videos: int) -> None:
        with self.lock:
            self._result = result
            self._error = None
            self.state = "done"
            self.stage = "完成"
            self._progress["collections"] = int(collections)
            self._progress["videos"] = int(videos)

    def fail(self, message: str) -> None:
        with self.lock:
            self._error = message
            self.state = "error"

    # ---- 读取 ----
    def snapshot(self) -> dict:
        with self.lock:
            return {
                "ok": True,
                "job": self.id,
                "state": self.state,
                "stage": self.stage,
                "log": list(self._log),
                "progress": dict(self._progress),
                "result": self._result,
                "error": self._error,
            }


# --------------------------------------------------------------------------
# 服务端逻辑
# --------------------------------------------------------------------------


class Server:
    """HTTP 服务端：路由、静态文件、任务管理、数据持久化。

    root     : 项目根目录（index.html / screenshots 等静态文件的根）
    data_dir : 数据目录（默认 root/data），测试可以传临时目录，避免污染真实 data/
    crawl_fn : 可注入的"解析 + 抓取"函数；测试用它替换真实联网爬取
               crawl_fn(query, subject, name, job) -> {"mid","author","via",
               "candidates","collections","truncated","fallback_search"}
    """

    def __init__(self, root=None, data_dir=None, sleep: float = SLEEP_DEFAULT,
                 max_videos: int = MAX_VIDEOS_DEFAULT, crawl_fn=None):
        self.root = Path(root).resolve() if root else HERE
        self.data_dir = Path(data_dir).resolve() if data_dir else (self.root / "data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sleep = max(0.0, float(sleep))
        self.max_videos = int(max_videos)
        self.crawl_fn = crawl_fn

        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._current: str | None = None

    # ---- 任务管理 ----
    def start_job(self, query: str, subject: str, name: str | None):
        """创建任务并立刻起线程；已经有任务在跑时返回 (None, True)。"""
        with self._lock:
            if self._current is not None:
                return None, True
            job = Job(uuid.uuid4().hex[:12], query, subject, name)
            self._jobs[job.id] = job
            self._current = job.id
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
        return job, False

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _crawl_log(self, job: Job):
        """给爬虫用的 log 回调：既写任务日志，也顺手把进度行翻译成 progress。"""

        def log(*parts):
            job.add_log(*parts)
            text = " ".join(str(p) for p in parts)
            m = PROGRESS_LINE_RE.search(text)
            if m:
                job.update_progress(collections=int(m.group(1)), videos_delta=int(m.group(4)))
                return
            m2 = PROGRESS_TOTAL_RE.search(text)
            if m2:
                job.update_progress(videos=int(m2.group(1)))

        return log

    def _default_crawl(self, query: str, subject: str, name: str | None, job: Job) -> dict:
        """真实流程：resolve_up → Crawler.crawl() →（空则）collect_by_search 兜底。"""
        info = C.resolve_up(query, log=job.add_log)
        job.set_stage("抓取合集")

        mid = int(info["mid"])
        known = B.SOURCES.get(mid)
        author = known["name"] if known else (info.get("author") or ("UP %d" % mid))
        if known and str(info.get("author") or "").startswith("mid "):
            job.add_log("已知来源：%s（mid=%d），显示名用已知名称" % (author, mid))

        crawler = C.Crawler(mid=mid, author=author, sleep=self.sleep,
                            log=self._crawl_log(job))
        collections, truncated = crawler.crawl()
        fallback = False
        if not collections:
            job.add_log("这个 UP 主没有公开合集，改用搜索接口兜底……")
            collections = C.collect_by_search(author, mid, log=self._crawl_log(job),
                                              sleep=self.sleep)
            fallback = True
        if not collections:
            raise C.CrawlError("这个 UP 主没有公开合集，搜索也没抓到视频")
        return {
            "mid": mid,
            "author": author,
            "via": info.get("via") or "mid",
            "candidates": info.get("candidates") or [],
            "collections": collections,
            "truncated": list(truncated or []),
            "fallback_search": fallback,
        }

    def _run_job(self, job: Job) -> None:
        started = time.time()
        print("[任务 %s] 开始：query=%r subject=%s" % (job.id, job.query, job.subject))
        sys.stdout.flush()
        try:
            job.add_log("任务开始：query=%s，subject=%s" % (job.query, job.subject))
            job.set_stage("解析 UP 主")
            crawl_fn = self.crawl_fn or self._default_crawl
            data = crawl_fn(job.query, job.subject, job.name, job)

            mid = int(data["mid"])
            author = data.get("author") or ("UP %d" % mid)
            collections = list(data.get("collections") or [])
            truncated = list(data.get("truncated") or [])
            fallback = bool(data.get("fallback_search"))
            if not collections:
                raise C.CrawlError("这个 UP 主没有公开合集，搜索也没抓到视频")

            collections, cut = cap_videos(collections, self.max_videos)
            if cut:
                truncated.extend(cut)
                job.add_log("达到单次任务上限 --max-videos=%d，已截断：%s"
                            % (self.max_videos, "、".join(cut)))
            total_videos = sum(len(c.get("videos") or []) for c in collections)

            # 2. 分类整理
            job.set_stage("分类整理")
            # 每次任务都重新读一遍自定义学科：用户在界面上刚加的学科，抓取时要能用
            extras = self._load_custom_subjects()
            labels = self._subject_labels(extras)
            titles = [str(v.get("title") or "")
                      for c in collections for v in (c.get("videos") or [])]
            detected, scores = B.detect_subject(titles)
            subject_id = job.subject if job.subject != "auto" else detected
            subject_label = labels.get(subject_id) or labels.get(detected, "其他")
            if job.subject == "auto":
                job.add_log("自动识别学科：%s（关键词命中 %s）" % (subject_label, scores))
            else:
                job.add_log("使用手动指定学科：%s（自动识别结果是 %s）"
                            % (subject_label, labels.get(detected, "其他")))

            known = B.SOURCES.get(mid)
            slug = slug_for(mid)
            source_id = known["id"] if known else slug
            if job.name:
                display_name = job.name
            elif known:
                display_name = known["name"]
            else:
                display_name = author

            raw = C.build_output(mid, author, collections, truncated)
            # 自定义学科必须把中文名一起传进去，否则切片里 subject 会被写成学科 id
            result = B.build_one_source(raw, subject_id=subject_id,
                                        name=job.name, slug=source_id,
                                        subject_label=subject_label,
                                        extra_subjects=extras)

            # 3. 写入数据
            # 写 raw / 清单 / 重建必须和删除、改学科等接口共用同一把锁，
            # 否则两个操作同时重建会把 resources.json 写坏或互相覆盖。
            job.set_stage("写入数据")
            with self._lock:
                filename = self._raw_filename(mid, slug)
                (self.data_dir / filename).write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                job.add_log("已写入 %s（%d 个合集 / %d 个视频）"
                            % (filename, len(collections), total_videos))

                manifest = self._load_manifest()
                manifest[str(mid)] = {"id": source_id, "name": display_name,
                                      "subject_id": subject_id, "subject": subject_label}
                self._write_manifest(manifest)
                self._rebuild(job)

            result["raw_path"] = self._raw_rel_path(filename)
            result["info"] = {
                "mid": mid,
                "author": display_name,
                "via": data.get("via") or "mid",
                "candidates": data.get("candidates") or [],
                "subject": subject_id,
                "subject_label": subject_label,
                "subject_scores": scores,
                "subject_detected": detected,
                "fallback_search": fallback,
                "collections": len(collections),
                "videos": total_videos,
                "truncated": truncated,
                "elapsed": round(time.time() - started, 3),
            }
            job.finish(result, collections=len(collections), videos=total_videos)
            print("[任务 %s] 完成：%s(mid=%s) %s / %d 个合集 / %d 个视频，耗时 %.1f 秒%s"
                  % (job.id, display_name, mid, subject_label, len(collections),
                     total_videos, time.time() - started,
                     "（搜索兜底）" if fallback else ""))
            sys.stdout.flush()
        except Exception as exc:  # 任何异常都变成任务错误，绝不让线程静默死掉
            message = str(exc) if isinstance(exc, C.CrawlError) else \
                "%s: %s" % (type(exc).__name__, exc)
            job.add_log("任务失败：%s" % message)
            job.fail(message)
            print("[任务 %s] 失败：%s（耗时 %.1f 秒）"
                  % (job.id, message, time.time() - started), file=sys.stderr)
            sys.stderr.flush()
        finally:
            with self._lock:
                if self._current == job.id:
                    self._current = None

    # ---- 数据文件 ----
    def _raw_filename(self, mid: int, slug: str) -> str:
        """raw 文件名：内置来源沿用历史文件名，其余用 raw_<slug>.json。"""
        legacy = LEGACY_RAW_FILES.get(int(mid))
        if legacy and (self.data_dir / legacy).exists():
            return legacy
        return "raw_%s.json" % slug

    def _raw_rel_path(self, filename: str) -> str:
        """给前端看的相对路径，形如 data/raw_huangfuren.json。"""
        full = (self.data_dir / filename).resolve()
        try:
            return full.relative_to(self.root).as_posix()
        except ValueError:
            return "data/" + filename

    def _manifest_path(self) -> Path:
        return self.data_dir / "sources.json"

    def _load_manifest(self) -> dict:
        try:
            data = json.loads(self._manifest_path().read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_manifest(self, manifest: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self._manifest_path(), manifest)

    # ---- 自定义学科（data/subjects.json） ----
    def _subjects_path(self) -> Path:
        """自定义学科清单固定放 data_dir 下，测试注入临时 data_dir 时自动隔离。"""
        return self.data_dir / "subjects.json"

    def _load_custom_subjects(self) -> list[dict]:
        """读 data/subjects.json 里的 custom 数组；坏了当没有（复用 build_resources 的容错）。"""
        return B.load_subjects_manifest(self._subjects_path())

    def _write_custom_subjects(self, items: list[dict]) -> None:
        write_json_atomic(self._subjects_path(), {"custom": list(items)})

    def _subject_labels(self, extras: list[dict] | None = None) -> dict[str, str]:
        """id → 中文名：内置 9 科 + 其他 + 所有自定义学科。"""
        labels = dict(B.SUBJECT_LABELS)
        for item in (extras if extras is not None else self._load_custom_subjects()):
            if isinstance(item, dict) and item.get("id"):
                labels[str(item["id"])] = str(item.get("name") or item["id"])
        return labels

    def _trash_dir(self) -> Path:
        return self.data_dir / TRASH_DIRNAME

    def known_subject_ids(self) -> set[str]:
        """/api/crawl 允许的 subject 取值：auto + 所有已知学科 id（内置 + 自定义）。"""
        return {"auto"} | set(self._subject_labels())

    def _scan_raws(self) -> list[tuple[Path, dict | None]]:
        """扫描 data/raw_*.json；按文件名排序保证重建结果稳定。坏文件返回 None。"""
        out: list[tuple[Path, dict | None]] = []
        for path in sorted(self.data_dir.glob("raw_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = None
            except Exception:
                data = None
            out.append((path, data))
        return out

    def _rebuild(self, job: Job | None = None, strict: bool = False) -> bool:
        """用 data/raw_*.json + 清单重建整份数据；单个坏文件只跳过，不让任务失败。

        注意：
        · 必须把 extra_subjects 传给 B.build，否则自定义学科会在重建后从
          resources.json 的 subjects 里"凭空消失"（build 只认内置表 + 额外传入的）。
        · 没有任何 raw 也要照常写出一份空数据集：用户可能把来源全删光，
          这时不能报错，resources.json 仍需包含 9 个内置学科供界面分组。
        strict=True 时重建失败抛 RebuildError，让写接口能返回 500 而不是假装成功。
        """
        log = job.add_log if job is not None else (lambda *a: None)
        overrides: dict[int, dict] = {}
        for key, value in self._load_manifest().items():
            try:
                overrides[int(key)] = value
            except (TypeError, ValueError):
                continue

        raws: list[dict] = []
        seen_mids: set[int] = set()
        for path, data in self._scan_raws():
            if data is None:
                log("跳过损坏的原始数据（不是合法 JSON）：%s" % path.name)
                continue
            mid = _mid_of(data)
            if mid is not None and mid in seen_mids:
                log("跳过重复来源 mid=%s 的旧文件：%s" % (mid, path.name))
                continue
            if mid is not None:
                seen_mids.add(mid)
            raws.append(data)

        if not raws:
            log("没有可用的原始数据，写出一份空数据集")
        try:
            data = B.build(raws, source_overrides=overrides,
                           extra_subjects=self._load_custom_subjects())
            B.write_outputs(data, self.data_dir)
        except Exception as exc:  # 数据重建失败也不能让抓取任务进入 error
            message = "重建数据失败：%s: %s" % (type(exc).__name__, exc)
            log(message + "（保留已有文件）")
            if strict:
                raise RebuildError(message) from exc
            return False
        stats = data["stats"]
        log("重建完成：%d 个来源 / %d 个合集 / %d 个视频，已写 resources.json + resources.js"
            % (stats["sources"], stats["courses"], stats["total"]))
        return True

    # ---- 来源 / 学科定位 ----
    def _find_raw_for_mid(self, mid: int | None) -> Path | None:
        """按 mid 找到对应的 raw 文件（重建时同一 mid 只认第一个文件）。"""
        if mid is None:
            return None
        for path, data in self._scan_raws():
            if data is not None and _mid_of(data) == mid:
                return path
        return None

    def _author_of_mid(self, mid: int) -> str:
        path = self._find_raw_for_mid(mid)
        if path is None:
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str((data.get("source") or {}).get("author") or "")

    def _find_source_mid(self, source_id: str) -> int | None:
        """把前端的"来源 id"（yishu / huangfuren / up<mid>）换算成 mid。"""
        wanted = str(source_id or "").strip()
        if not wanted:
            return None
        # 1) 当前已生成的数据最权威，行里有 mid
        try:
            data = json.loads((self.data_dir / "resources.json").read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for src in (data.get("sources") or []):
            if isinstance(src, dict) and str(src.get("id")) == wanted:
                try:
                    return int(src.get("mid"))
                except (TypeError, ValueError):
                    break
        # 2) 清单（用户自己加的来源可能还没重建成功）
        for key, value in self._load_manifest().items():
            if isinstance(value, dict) and str(value.get("id")) == wanted and str(key).isdigit():
                return int(key)
        # 3) 内置来源表（一数 / 黄夫人）
        for mid, info in B.SOURCES.items():
            if info.get("id") == wanted:
                return int(mid)
        return None

    def _source_entry(self, mid: int, subject_id: str, subject_name: str) -> dict:
        """构造清单项：保留已有 id/name，只覆盖学科；内置来源用来源表里的 name/id。"""
        manifest = self._load_manifest()
        entry = dict(manifest.get(str(mid)) or {})
        known = B.SOURCES.get(int(mid))
        if not entry.get("id"):
            entry["id"] = known["id"] if known else ("up%d" % int(mid))
        if not entry.get("name"):
            entry["name"] = (known["name"] if known else "") or \
                self._author_of_mid(int(mid)) or ("UP %d" % int(mid))
        entry["subject_id"] = subject_id
        entry["subject"] = subject_name
        return entry

    def _source_row(self, source_id: str) -> dict | None:
        try:
            data = json.loads((self.data_dir / "resources.json").read_text(encoding="utf-8"))
        except Exception:
            return None
        for src in (data.get("sources") or []):
            if isinstance(src, dict) and str(src.get("id")) == str(source_id):
                return src
        return None

    def _sources_using_subject(self, subject_id: str) -> list[str]:
        """列出还在用这个学科的来源名字（删学科前要拦下来，不能静默改数据）。"""
        overrides = self._load_manifest()
        names: list[str] = []
        seen: set[str] = set()
        for _path, data in self._scan_raws():
            if data is None:
                continue
            mid = _mid_of(data)
            if mid is None:
                continue
            known = B.SOURCES.get(mid) or {}
            override = overrides.get(str(mid)) or {}
            effective = override.get("subject_id") or known.get("subject_id") or "other"
            if effective != subject_id:
                continue
            name = override.get("name") or known.get("name") or \
                (data.get("source") or {}).get("author") or ("UP %d" % mid)
            if name not in seen:
                seen.add(name)
                names.append(str(name))
        return names

    # ---- 接口数据 ----
    def list_sources(self) -> list[dict]:
        try:
            data = json.loads((self.data_dir / "resources.json").read_text(encoding="utf-8"))
        except Exception:
            return []
        out = []
        for src in (data.get("sources") or []):
            if not isinstance(src, dict):
                continue
            out.append({
                "id": src.get("id"),
                "name": src.get("name"),
                "subject_id": src.get("subject_id"),
                "subject": src.get("subject"),
                "count": src.get("count", 0),
                "courses": src.get("courses", 0),
            })
        return out

    def list_subjects(self) -> list[dict]:
        """读 resources.json 的 subjects；文件不在时回退成内置学科（+ 自定义，计数全 0）。"""
        try:
            data = json.loads((self.data_dir / "resources.json").read_text(encoding="utf-8"))
        except Exception:
            data = None
        rows = data.get("subjects") if isinstance(data, dict) else None
        if not rows:
            rows = [{"id": sid, "name": name, "builtin": True, "count": 0,
                     "sources": 0, "courses": 0} for sid, name in B.SUBJECTS]
            existing = {row["id"] for row in rows}
            for item in self._load_custom_subjects():
                if item.get("id") and item["id"] not in existing:
                    rows.append({"id": item["id"], "name": item["name"], "builtin": False,
                                 "count": 0, "sources": 0, "courses": 0})
        out = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            out.append({
                "id": row.get("id"),
                "name": row.get("name") or row.get("id"),
                "builtin": bool(row.get("builtin")),
                "count": int(row.get("count") or 0),
                "sources": int(row.get("sources") or 0),
                "courses": int(row.get("courses") or 0),
            })
        return out

    def _make_custom_id(self, name: str, taken: set[str]) -> str:
        """自动生成稳定 id：custom- + md5(名字) 前 8 位（同名永远得到同一个 id）。"""
        digest = hashlib.md5(name.encode("utf-8")).hexdigest()
        base = CUSTOM_SUBJECT_PREFIX + digest[:8]
        if base not in taken:
            return base
        n = 1
        while True:
            candidate = "%s%s-%d" % (CUSTOM_SUBJECT_PREFIX, digest[:8], n)
            if candidate not in taken:
                return candidate
            n += 1

    def add_subject(self, name: str, subject_id: str | None = None) -> dict:
        """新增自定义学科；与内置/已有自定义重名时幂等返回已有的那个。"""
        name = (name or "").strip()
        if not name:
            raise HttpError(400, "学科名不能为空")
        with self._lock:
            for builtin_id, builtin_name in B.SUBJECTS:
                if builtin_name == name:
                    return {"ok": True, "subject": {"id": builtin_id, "name": builtin_name,
                                                    "builtin": True}}
            customs = self._load_custom_subjects()
            for item in customs:
                if item.get("name") == name:
                    return {"ok": True, "subject": {"id": item["id"], "name": item["name"],
                                                    "builtin": False}}
            builtin_ids = {sid for sid, _n in B.SUBJECTS}
            existing_ids = {item["id"] for item in customs}
            taken = builtin_ids | existing_ids
            if subject_id is not None:
                subject_id = str(subject_id).strip()
                if not SUBJECT_ID_RE.match(subject_id):
                    raise HttpError(400, "id 只能包含小写字母、数字、下划线或连字符")
                if subject_id in taken:
                    raise HttpError(400, "学科 id「%s」已经被占用" % subject_id)
            else:
                subject_id = self._make_custom_id(name, taken)
            customs.append({"id": subject_id, "name": name})
            self._write_custom_subjects(customs)
            # 重建必须带上这份清单，否则刚加的学科不会进 resources.json 的 subjects
            self._rebuild(strict=True)
            return {"ok": True, "subject": {"id": subject_id, "name": name, "builtin": False}}

    def delete_subject(self, subject_id: str) -> dict:
        subject_id = str(subject_id or "").strip()
        with self._lock:
            if subject_id in dict(B.SUBJECTS):
                raise HttpError(400, "内置学科不能删除")
            customs = self._load_custom_subjects()
            target = [item for item in customs if item.get("id") == subject_id]
            if not target:
                raise HttpError(404, "没有这个自定义学科")
            users = self._sources_using_subject(subject_id)
            if users:
                raise HttpError(409, "还有来源在使用这个学科：%s" % "、".join(users))
            self._write_custom_subjects(
                [item for item in customs if item.get("id") != subject_id])
            self._rebuild(strict=True)
            return {"ok": True, "removed": {"id": subject_id, "name": target[0]["name"]}}

    def set_source_subject(self, source_id: str, subject_id: str) -> dict:
        """给来源改学科：写进清单（source_overrides 会覆盖内置来源的默认学科）。"""
        subject_id = str(subject_id or "").strip()
        labels = self._subject_labels()
        if subject_id not in labels:
            raise HttpError(400, "没有这个学科：%s" % (subject_id or "(空)"))
        with self._lock:
            mid = self._find_source_mid(source_id)
            if mid is None or self._find_raw_for_mid(mid) is None:
                raise HttpError(404, "没有这个来源：%s" % (source_id or "(空)"))
            manifest = self._load_manifest()
            manifest[str(mid)] = self._source_entry(mid, subject_id, labels[subject_id])
            self._write_manifest(manifest)
            self._rebuild(strict=True)
            return {"ok": True, "source": self._source_row(source_id)}

    def delete_source(self, source_id: str) -> dict:
        """删除一个已导入的 UP 主：raw 软删除进 data/trash/，可恢复。"""
        with self._lock:
            mid = self._find_source_mid(source_id)
            path = self._find_raw_for_mid(mid)
            if mid is None or path is None:
                raise HttpError(404, "没有这个来源：%s" % (source_id or "(空)"))
            # 软删除而不是直接 unlink：用户点错了还能从 data/trash/ 捞回来，
            # 也方便排查"某天数据怎么少了一个来源"。
            trash = self._trash_dir()
            trash.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime(TRASH_STAMP_FMT)
            dest = trash / ("%s.%s" % (path.name, stamp))
            n = 1
            while dest.exists():
                dest = trash / ("%s.%s-%d" % (path.name, stamp, n))
                n += 1
            try:
                path.replace(dest)
            except OSError as exc:
                raise RebuildError("删除来源失败：无法移动 %s —— %s" % (path.name, exc)) from exc
            manifest = self._load_manifest()
            manifest.pop(str(mid), None)
            self._write_manifest(manifest)
            # 删到只剩 0 个来源也要能重建（_rebuild 会写出空数据集）
            self._rebuild(strict=True)
            return {"ok": True, "removed": path.name,
                    "trash": "data/%s/%s" % (TRASH_DIRNAME, dest.name)}

    def reset(self) -> list[str]:
        """删掉用户添加的 raw_*.json（保留两个内置的）+ 清空自定义学科，并重建数据。"""
        with self._lock:
            removed = []
            for path in sorted(self.data_dir.glob("raw_*.json")):
                if path.name in PRESET_RAW_FILES:
                    continue
                try:
                    path.unlink()
                    removed.append(path.name)
                except OSError:
                    continue
            # 自定义学科也算"用户加的"，reset 时一起清掉，回到出厂状态
            self._write_custom_subjects([])
            # 清单里删掉的来源也要清掉，否则下次同名 mid 会继承旧学科
            mids: set[int] = set()
            for _path, data in self._scan_raws():
                mid = _mid_of(data) if data is not None else None
                if mid is not None:
                    mids.add(mid)
            manifest = {k: v for k, v in self._load_manifest().items()
                        if str(k).isdigit() and int(k) in mids}
            self._write_manifest(manifest)
            self._rebuild(None)
            return removed

    # ---- 静态文件 ----
    def resolve_static(self, request_path: str):
        """把 URL 路径映射到磁盘文件；越界 / 目录 / 不存在都返回 None。

        安全要点：先 unquote 再判空字节与反斜杠，然后 resolve() 后判断是否仍在
        对应根目录内（. 和 .. 都会被解析掉）。任何越界都当 404，不泄露"存在但禁止"。
        另外：/data/... 映射到 data_dir（测试里是临时目录），其余映射到项目根目录。
        """
        if "\x00" in request_path or "\\" in request_path:
            return None  # Windows 下反斜杠可能绕过路径判断，直接拒绝
        rel = request_path.lstrip("/")
        if rel == "":
            base, sub, no_store = self.root, "index.html", True
        elif rel == "data" or rel.startswith("data/"):
            base = self.data_dir
            sub = rel[5:] if rel.startswith("data/") else ""
            no_store = True
        else:
            base, sub, no_store = self.root, rel, False
        if not sub:
            return None  # /data/ → 目录请求，不做列表

        candidate = Path(sub)
        if candidate.is_absolute() or candidate.drive:
            return None
        try:
            resolved = (base / candidate).resolve()
        except (OSError, ValueError):
            return None
        try:
            resolved.relative_to(base)
        except ValueError:
            return None
        if not resolved.is_file():
            return None
        if resolved.name == "index.html":
            no_store = True
        return resolved, no_store


def _mid_of(raw: dict) -> int | None:
    try:
        return int((raw.get("source") or {}).get("mid"))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# HTTP 处理
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "ResourceLab/1.0"
    sys_version = ""

    def log_message(self, fmt, *args):  # noqa: A003 - 每个请求都打一行太吵，静音
        return

    @property
    def app(self) -> Server:
        return self.server.app  # type: ignore[attr-defined]

    # ---- 响应原语 ----
    def _send(self, code: int, content_type: str, body, no_store: bool = False) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if no_store:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端提前断开，忽略

    def _send_json(self, code: int, payload: dict) -> None:
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(payload, ensure_ascii=False), no_store=True)

    def _send_404(self) -> None:
        self._send(404, "text/html; charset=utf-8", NOT_FOUND_HTML, no_store=True)

    def _send_500(self) -> None:
        self._send(500, "text/html; charset=utf-8", SERVER_ERROR_HTML, no_store=True)

    # ---- GET ----
    def do_GET(self):  # noqa: N802 - http.server 的约定命名
        try:
            self._route_get()
        except Exception as exc:
            sys.stderr.write("处理 GET %s 出错：%s: %s\n"
                             % (self.path, type(exc).__name__, exc))
            self._send_500()

    def _route_get(self) -> None:
        path = unquote(urlsplit(self.path).path)

        if path == "/api/ping":
            self._send_json(200, {"ok": True, "name": "resource-lab",
                                  "version": SCHEMA_VERSION})
            return
        if path == "/api/sources":
            self._send_json(200, {"ok": True, "sources": self.app.list_sources()})
            return
        if path == "/api/subjects":
            self._send_json(200, {"ok": True, "subjects": self.app.list_subjects()})
            return
        if path.startswith("/api/job/"):
            job_id = path[len("/api/job/"):]
            job = self.app.get_job(job_id) if job_id and "/" not in job_id else None
            if job is None:
                self._send_json(404, {"ok": False, "error": "没有这个任务"})
                return
            self._send_json(200, job.snapshot())
            return
        if path.startswith("/api/"):
            self._send_json(404, {"ok": False, "error": "没有这个接口"})
            return

        found = self.app.resolve_static(path)
        if found is None:
            self._send_404()
            return
        target, no_store = found
        try:
            body = target.read_bytes()
        except OSError:
            self._send_404()
            return
        self._send(200, MIME_TYPES.get(target.suffix.lower(), DEFAULT_MIME),
                   body, no_store=no_store)

    # ---- POST ----
    def do_POST(self):  # noqa: N802
        try:
            self._route_post()
        except Exception as exc:
            sys.stderr.write("处理 POST %s 出错：%s: %s\n"
                             % (self.path, type(exc).__name__, exc))
            self._send_500()

    # ---- DELETE（前端用 DELETE /api/subjects/<id> 删自定义学科） ----
    def do_DELETE(self):  # noqa: N802
        try:
            self._route_delete()
        except Exception as exc:
            sys.stderr.write("处理 DELETE %s 出错：%s: %s\n"
                             % (self.path, type(exc).__name__, exc))
            self._send_500()

    def _route_delete(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path.startswith("/api/subjects/"):
            subject_id = path[len("/api/subjects/"):]
            if not subject_id or "/" in subject_id:
                self._send_json(404, {"ok": False, "error": "没有这个接口"})
                return
            self._json_op(self.app.delete_subject, subject_id)
            return
        self._send_json(404, {"ok": False, "error": "没有这个接口"})

    def _json_op(self, fn, *args) -> None:
        """统一执行一个会写数据的接口：业务错误带状态码，重建失败给 500 + 人话错误。"""
        try:
            payload = fn(*args)
        except HttpError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
            return
        except RebuildError as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # 兜底：任何意外都返回 JSON，别让连接悬着
            sys.stderr.write("接口 %s 出错：%s: %s\n"
                             % (getattr(fn, "__name__", fn), type(exc).__name__, exc))
            self._send_json(500, {"ok": False,
                                  "error": "服务内部错误：%s: %s" % (type(exc).__name__, exc)})
            return
        self._send_json(200, payload)

    def _parse_json_object(self, body: bytes):
        """POST 体 → dict；空体 / 坏 JSON / 非对象都返回人话错误。"""
        if not body:
            return None, "请求体是空的，需要 JSON 对象"
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, "请求体不是合法的 JSON"
        if not isinstance(payload, dict):
            return None, "请求体必须是 JSON 对象"
        return payload, None

    def _route_post(self) -> None:
        path = unquote(urlsplit(self.path).path)
        body, error = self._read_body()
        if error is not None:
            self._send_json(400, {"ok": False, "error": error})
            return
        if path == "/api/crawl":
            self._handle_crawl(body)
            return
        if path == "/api/reset":
            removed = self.app.reset()
            self._send_json(200, {"ok": True, "removed": removed, "subjects_cleared": True})
            return
        if path == "/api/subjects":
            self._handle_subject_add(body)
            return
        if path == "/api/subjects/delete":
            self._handle_subject_delete(body)
            return
        if path == "/api/source/subject":
            self._handle_source_subject(body)
            return
        if path == "/api/source/delete":
            self._handle_source_delete(body)
            return
        self._send_json(404, {"ok": False, "error": "没有这个接口"})

    def _handle_subject_add(self, body: bytes) -> None:
        payload, error = self._parse_json_object(body)
        if error is not None:
            self._send_json(400, {"ok": False, "error": error})
            return
        name = payload.get("name")
        if name is not None and not isinstance(name, str):
            self._send_json(400, {"ok": False, "error": "name 必须是字符串"})
            return
        subject_id = payload.get("id")
        if subject_id is not None and not isinstance(subject_id, str):
            self._send_json(400, {"ok": False, "error": "id 必须是字符串"})
            return
        self._json_op(self.app.add_subject, name or "", subject_id)

    def _handle_subject_delete(self, body: bytes) -> None:
        payload, error = self._parse_json_object(body)
        if error is not None:
            self._send_json(400, {"ok": False, "error": error})
            return
        subject_id = payload.get("id")
        if not isinstance(subject_id, str) or not subject_id.strip():
            self._send_json(400, {"ok": False, "error": "缺少 id：要删除哪个自定义学科"})
            return
        self._json_op(self.app.delete_subject, subject_id)

    def _handle_source_subject(self, body: bytes) -> None:
        payload, error = self._parse_json_object(body)
        if error is not None:
            self._send_json(400, {"ok": False, "error": error})
            return
        source_id = payload.get("id")
        subject_id = payload.get("subject_id")
        if not isinstance(source_id, str) or not source_id.strip():
            self._send_json(400, {"ok": False, "error": "缺少 id：要改哪个来源"})
            return
        if not isinstance(subject_id, str) or not subject_id.strip():
            self._send_json(400, {"ok": False, "error": "缺少 subject_id：要改成哪个学科"})
            return
        self._json_op(self.app.set_source_subject, source_id, subject_id)

    def _handle_source_delete(self, body: bytes) -> None:
        payload, error = self._parse_json_object(body)
        if error is not None:
            self._send_json(400, {"ok": False, "error": error})
            return
        source_id = payload.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            self._send_json(400, {"ok": False, "error": "缺少 id：要删除哪个来源"})
            return
        self._json_op(self.app.delete_source, source_id)

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            if self.headers.get("Transfer-Encoding"):
                return b"", "不支持分块编码的请求体"
            return b"", None
        try:
            size = int(length)
        except ValueError:
            return b"", "Content-Length 不是合法数字"
        if size < 0:
            return b"", "Content-Length 非法"
        if size == 0:
            return b"", None
        return self.rfile.read(size), None

    def _handle_crawl(self, body: bytes) -> None:
        if not body:
            self._send_json(400, {"ok": False,
                                  "error": "请求体是空的，需要 JSON：{\"query\":\"UP 主名称或 ID\"}"})
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"ok": False, "error": "请求体不是合法的 JSON"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "请求体必须是 JSON 对象"})
            return

        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            self._send_json(400, {"ok": False,
                                  "error": "缺少 query：请输入 UP 主名称、ID 或主页链接"})
            return
        subject = payload.get("subject", "auto")
        # 校验放宽成"auto 或任意已知学科 id"：自定义学科是用户自己加的，必须能用
        if not isinstance(subject, str) or subject not in self.app.known_subject_ids():
            self._send_json(400, {"ok": False,
                                  "error": "subject 只能是 auto 或已知学科 id（内置 / 自定义）"})
            return
        name = payload.get("name")
        if name is not None and not isinstance(name, str):
            self._send_json(400, {"ok": False, "error": "name 必须是字符串"})
            return

        job, _busy = self.app.start_job(query.strip(), subject,
                                        (name or "").strip() or None)
        if job is None:
            self._send_json(409, {"ok": False,
                                  "error": "已经有一个抓取任务在跑，请等它结束"})
            return
        self._send_json(200, {"ok": True, "job": job.id})


def make_server(host: str, port: int, app: Server) -> ThreadingHTTPServer:
    """建一个 ThreadingHTTPServer 并挂上 Server 实例（测试也用这个）。"""
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.app = app  # type: ignore[attr-defined]
    return httpd


def _port_in_use(host: str, port: int) -> bool:
    """端口是不是已经有人在监听（--port 被占用时自动往后试的依据）。

    不能靠 bind 判断：ThreadingHTTPServer 会设 SO_REUSEADDR，而 Windows 下
    SO_REUSEADDR 允许两个进程绑同一个端口（第二个会"静默抢绑"成功），
    于是"端口被占用就换一个"永远触发不了。所以改成真的连一下：
    能连上 = 有人监听；连不上（拒绝/超时）= 端口空着。
    """
    if port <= 0:
        return False
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "", "::", "::0") else host
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.3)
    try:
        probe.connect((probe_host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def serve_forever(host: str, port: int, app: Server, open_browser: bool = False) -> int:
    httpd = None
    for offset in range(PORT_TRIES + 1):
        candidate = port + offset
        if _port_in_use(host, candidate):
            continue
        try:
            httpd = make_server(host, candidate, app)
            break
        except OSError as exc:
            if offset == PORT_TRIES:
                print("错误：%d~%d 都绑定失败：%s" % (port, port + PORT_TRIES, exc),
                      file=sys.stderr)
                return 1
    if httpd is None:
        print("错误：%d~%d 端口都被占用了，换一个 --port 再试" % (port, port + PORT_TRIES),
              file=sys.stderr)
        return 1
    actual_port = int(httpd.server_address[1])
    if port and actual_port != port:
        print("端口 %d 被占用，已改用 %d" % (port, actual_port))
    print_banner(host, actual_port, app.sleep, app.max_videos)
    sys.stdout.flush()
    if open_browser:
        try:
            webbrowser.open("http://127.0.0.1:%d/" % actual_port)
        except Exception:
            pass
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，服务已停止。")
    finally:
        httpd.server_close()
    return 0


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="serve.py",
        description="学习资源库本地服务：在网页里输入 B 站 UP 主名称/ID，自动抓取并整理。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python serve.py                     # 127.0.0.1:8765\n"
               "  python serve.py --port 8799 --open  # 指定端口并自动开浏览器\n")
    parser.add_argument("--host", default=HOST_DEFAULT,
                        help="监听地址（默认 127.0.0.1，只允许本机访问）")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT,
                        help="监听端口（默认 8765；被占用时自动往后试 %d 个）" % PORT_TRIES)
    parser.add_argument("--open", action="store_true", help="启动后用默认浏览器打开首页")
    parser.add_argument("--sleep", type=float, default=SLEEP_DEFAULT,
                        help="请求间隔秒数（默认 %.1f，别调太小否则容易被风控）" % SLEEP_DEFAULT)
    parser.add_argument("--max-videos", type=int, default=MAX_VIDEOS_DEFAULT,
                        help="单次任务最多抓多少视频（默认 %d，防止超大 UP 卡死）"
                             % MAX_VIDEOS_DEFAULT)
    return parser


def print_banner(host: str, port: int, sleep: float, max_videos: int) -> None:
    local = "http://127.0.0.1:%d" % port
    print("=" * 66)
    print("  学习资源库 · 本地服务  (serve.py)")
    print("=" * 66)
    print("  监听地址 : http://%s:%d" % (host, port))
    print("  站点首页 : %s/" % local)
    print("  抓取间隔 : %.1f 秒 / 单次任务视频上限 : %d" % (sleep, max_videos))
    print("-" * 66)
    print("  API：")
    print("    GET  /api/ping          探测服务是否在跑")
    print("    POST /api/crawl         提交抓取任务 {'query': '一数' 或 '14229967'}")
    print("    GET  /api/job/<id>      查询任务状态 / 日志 / 进度 / 结果")
    print("    GET  /api/sources       当前来源清单")
    print("    POST /api/reset         清掉用户添加的来源并重建数据")
    print("    GET  /api/subjects      学科清单（内置 + 自定义）")
    print("    POST /api/subjects      新增自定义学科 {'name': '信息技术'}")
    print("    DELETE /api/subjects/<id>  删除自定义学科")
    print("    POST /api/source/subject   给来源改学科")
    print("    POST /api/source/delete    删除已导入的 UP 主（软删除可恢复）")
    print("-" * 66)
    print("  用法：在网页里输入 B 站 UP 主的名称 / ID / 主页链接，点抓取即可。")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("!" * 66)
        print("  ⚠ 安全警告：正在监听 %s，局域网内任何人都能触发抓取、看到全部数据。" % host)
        print("    只想自己用就去掉 --host %s（默认只监听 127.0.0.1）。" % host)
        print("!" * 66)
    print("  按 Ctrl+C 停止服务。")
    print("=" * 66)
    sys.stdout.flush()


def main(argv=None) -> int:
    _setup_console()
    args = build_arg_parser().parse_args(argv)
    app = Server(sleep=args.sleep, max_videos=args.max_videos)
    return serve_forever(args.host, args.port, app, open_browser=args.open)


if __name__ == "__main__":
    sys.exit(main())
