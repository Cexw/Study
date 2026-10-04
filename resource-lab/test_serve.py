#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve.py 的离线测试：python test_serve.py

原则：**绝对不联网**。抓取那一步通过 `Server(crawl_fn=...)` 注入假实现，
但 HTTP 服务是真的（ThreadingHTTPServer 监听 127.0.0.1:0 拿临时端口），
请求也是真的（urllib.request），测试数据全部写在临时目录里，不碰真实 data/。

覆盖：静态服务 + 路径安全、/api/ping、抓取生命周期（含 409）、失败路径、
参数校验、用户指定学科优先、落盘与重建（含坏文件容错）、/api/reset、
学科管理（/api/subjects 增删/幂等/校验）、来源改学科、来源软删除（data/trash/）、
自定义学科端到端（crawl + subject_label）。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_resources as B  # noqa: E402
import crawl_bilibili as C  # noqa: E402
import serve as S  # noqa: E402

PASS = 0
FAIL = 0


def ok(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  \u2713 %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  \u2717 %s  %s" % (name, extra))


# --------------------------------------------------------------------------
# 测试脚手架
# --------------------------------------------------------------------------


class Lab:
    """一个跑在临时端口上的测试服务。"""

    def __init__(self, crawl_fn, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.app = S.Server(data_dir=self.data_dir, crawl_fn=crawl_fn)
        self.httpd = S.make_server("127.0.0.1", 0, self.app)
        self.port = int(self.httpd.server_address[1])
        self.base = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def close(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


LABS: list[Lab] = []


def new_lab(crawl_fn=None, data_dir=None) -> Lab:
    if data_dir is None:
        data_dir = Path(tempfile.mkdtemp(prefix="resource-lab-test-"))
    lab = Lab(crawl_fn, data_dir)
    LABS.append(lab)
    return lab


def http(method: str, url: str, payload=None, raw=None, ctype: str = "application/json"):
    body = None
    if raw is not None:
        body = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
    elif payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": ctype} if body is not None else {}
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def wait_job(lab: Lab, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    snap = {}
    while time.time() < deadline:
        status, _h, body = http("GET", lab.base + "/api/job/" + job_id)
        snap = json.loads(body.decode("utf-8"))
        if snap.get("state") != "running":
            return snap
        time.sleep(0.05)
    return snap


def video(bvid: str, title: str, duration: int = 600) -> dict:
    return {"bvid": bvid, "title": title, "duration": duration, "pubdate": 1700000000,
            "cover": "", "views": 10, "danmaku": 1,
            "url": "https://www.bilibili.com/video/" + bvid}


MATH_TITLES = ["【导数】切线问题怎么想", "【数列】错位相减求和", "【集合】交并补运算"]


def fake_crawl_fn(mid: int = 999999, author: str = "测试UP", titles=None,
                  error: Exception | None = None, delay: float = 0.25):
    """假的"解析 + 抓取"：会 sleep 并打日志，用来验证真实的并发/轮询行为。"""
    titles = list(titles or MATH_TITLES)
    videos = [video("BVFAKE%03d" % i, t, 600 + i) for i, t in enumerate(titles)]
    collections = [{
        "season_id": 7, "title": "假合集", "intro": "", "cover": "",
        "total": len(videos), "fetched": len(videos), "truncated": False,
        "videos": videos,
    }]

    def fake(query, subject, name, job):
        job.add_log("假爬虫开始：query=%s subject=%s" % (query, subject))
        time.sleep(delay)
        if error is not None:
            raise error
        job.add_log("  [1/1] 假合集 —— %d/%d" % (len(videos), len(videos)))
        job.update_progress(collections=1, videos=len(videos))
        time.sleep(delay)
        job.add_log("假爬虫结束")
        return {"mid": mid, "author": author, "via": "mid", "candidates": [],
                "collections": collections, "truncated": [], "fallback_search": False}

    return fake


def minimal_raw(mid: int, author: str, bvid: str | None = None) -> dict:
    bvid = bvid or ("BVX%d" % mid)
    return {
        "schema": C.SCHEMA,
        "crawled_at": 1700000000000,
        "source": {"platform": "bilibili", "author": author, "mid": mid,
                   "space": "https://space.bilibili.com/%d" % mid},
        "collections": [{
            "season_id": 1, "title": "合集", "intro": "", "cover": "",
            "total": 1, "fetched": 1, "truncated": False,
            "videos": [{"bvid": bvid, "title": "【导数】切线", "duration": 600,
                        "pubdate": 1700000000, "cover": "", "views": 0, "danmaku": 0,
                        "url": "https://www.bilibili.com/video/" + bvid}],
        }],
        "stats": {"collections": 1, "videos": 1, "truncated_collections": []},
    }


# --------------------------------------------------------------------------
# [1] 静态文件与路径安全
# --------------------------------------------------------------------------


def test_static() -> None:
    print("\n[1] 静态文件服务与路径安全")
    tmp = Path(tempfile.mkdtemp(prefix="lab-static-"))
    (tmp / "resources.json").write_text(
        json.dumps({"schema": "test-fixture", "sources": []}), encoding="utf-8")
    lab = new_lab(crawl_fn=None, data_dir=tmp)
    base = lab.base

    status, headers, body = http("GET", base + "/")
    ok("/ 返回 200", status == 200, str(status))
    ok("/ 是 HTML", headers.get("Content-Type", "").startswith("text/html"),
       headers.get("Content-Type", ""))
    ok("/ 返回的是真实 index.html（>1000 字节）", len(body) > 1000, "%d 字节" % len(body))
    ok("/ 加了 no-store", headers.get("Cache-Control") == "no-store")
    ok("/ 没泄露 Traceback", b"Traceback" not in body)

    status, headers, body = http("GET", base + "/data/resources.json")
    ok("/data/resources.json 返回 200", status == 200, str(status))
    ok("/data/resources.json 是 application/json",
       headers.get("Content-Type", "").startswith("application/json"),
       headers.get("Content-Type", ""))
    ok("/data/resources.json 加了 no-store", headers.get("Cache-Control") == "no-store")
    ok("/data/resources.json 内容正确", json.loads(body.decode("utf-8"))["schema"] == "test-fixture")

    status, headers, body = http("GET", base + "/screenshots/site-dark.png")
    ok("png 返回 200", status == 200, str(status))
    ok("png Content-Type 正确", headers.get("Content-Type") == "image/png",
       headers.get("Content-Type", ""))
    ok("png 二进制没被当文本（PNG 魔数 + 长度一致）",
       body[:8] == b"\x89PNG\r\n\x1a\n" and int(headers.get("Content-Length", "0")) == len(body),
       "%d 字节" % len(body))

    status, _h, body = http("GET", base + "/nope.js")
    ok("不存在的文件 404", status == 404, str(status))
    ok("404 是中文提示页", "没有找到" in body.decode("utf-8", "replace"))
    ok("404 页没有 Traceback", b"Traceback" not in body)

    status, _h, body = http("GET", base + "/../crawl_bilibili.py")
    ok("/../ 越界返回 404", status == 404, str(status))
    ok("/../ 没有返回源码", b"def resolve_up" not in body)

    status, _h, body = http("GET", base + "/..%2f..%2fetc/passwd")
    ok("%2f 编码绕过返回 404", status == 404, str(status))
    ok("没有泄露 /etc/passwd", b"root:" not in body)

    status, _h, body = http("GET", base + "/data/")
    ok("/data/ 目录请求 404（不做目录列表）", status == 404, str(status))
    ok("/data/ 没有列出目录内容", b"resources.json" not in body)

    status, _h, body = http("GET", base + "/data/../build_resources.py")
    ok("/data/../ 越界返回 404", status == 404, str(status))
    ok("没有返回 build_resources.py 源码", b"def build(" not in body)

    status, _h, body = http("GET", base + "/..\\..\\build_resources.py")
    ok("Windows 反斜杠绕过返回 404", status == 404, str(status))
    ok("反斜杠请求没有返回源码", b"def build(" not in body)

    status, _h, body = http("GET", base + "/api/does-not-exist")
    ok("未知 API 404 + ok:false",
       status == 404 and json.loads(body.decode("utf-8")).get("ok") is False)


# --------------------------------------------------------------------------
# [2] /api/ping
# --------------------------------------------------------------------------


def test_ping() -> None:
    print("\n[2] /api/ping")
    lab = new_lab(crawl_fn=None)
    status, headers, body = http("GET", lab.base + "/api/ping")
    ok("ping 200", status == 200, str(status))
    ok("ping 是 JSON", headers.get("Content-Type", "").startswith("application/json"))
    payload = json.loads(body.decode("utf-8"))
    ok("ping 字段完全符合约定",
       payload == {"ok": True, "name": "resource-lab", "version": 1}, str(payload))


# --------------------------------------------------------------------------
# [3] 抓取生命周期
# --------------------------------------------------------------------------


def test_lifecycle() -> None:
    print("\n[3] 抓取生命周期（后台任务 + 409 + 轮询）")
    lab = new_lab(crawl_fn=fake_crawl_fn(mid=999999, author="测试UP"))

    started = time.time()
    status, _h, body = http("POST", lab.base + "/api/crawl", payload={"query": "测试UP"})
    elapsed = time.time() - started
    ok("POST /api/crawl 200", status == 200, str(status))
    payload = json.loads(body.decode("utf-8"))
    ok("立刻返回 job id", bool(payload.get("ok")) and isinstance(payload.get("job"), str),
       str(payload))
    ok("POST 没等抓取就返回（<0.5s）", elapsed < 0.5, "%.3fs" % elapsed)
    job_id = payload["job"]

    status, _h, body = http("POST", lab.base + "/api/crawl", payload={"query": "另一个"})
    ok("已有任务在跑时第二个请求 409", status == 409, str(status))
    ok("409 错误文案完全一致",
       json.loads(body.decode("utf-8")).get("error") == "已经有一个抓取任务在跑，请等它结束")

    status, _h, body = http("GET", lab.base + "/api/job/" + job_id)
    snap = json.loads(body.decode("utf-8"))
    ok("运行中 state=running", snap.get("state") == "running", str(snap.get("state")))
    ok("运行中 log 非空", len(snap.get("log") or []) > 0)
    ok("运行中 stage 非空", bool(snap.get("stage")), str(snap.get("stage")))
    ok("progress 含 collections/videos",
       {"collections", "videos"} <= set(snap.get("progress") or {}))
    ok("运行中 result 还是 None", snap.get("result") is None)
    ok("运行中 error 还是 None", snap.get("error") is None)

    final = wait_job(lab, job_id)
    ok("最终 state=done", final.get("state") == "done", str(final.get("state")))
    ok("done 时 error=None", final.get("error") is None)
    result = final.get("result") or {}
    ok("result 含 source/videos/stats/raw_path/info",
       all(k in result for k in ("source", "videos", "stats", "raw_path", "info")))
    info = result.get("info") or {}
    ok("info.mid 正确", info.get("mid") == 999999, str(info.get("mid")))
    ok("info.subject 自动识别为 math", info.get("subject") == "math", str(info.get("subject")))
    ok("info.subject_detected=math", info.get("subject_detected") == "math",
       str(info.get("subject_detected")))
    ok("info.subject_scores 有 math 命中", (info.get("subject_scores") or {}).get("math", 0) > 0)
    ok("info.fallback_search=false", info.get("fallback_search") is False)
    ok("result.source.name 用爬到的作者名",
       (result.get("source") or {}).get("name") == "测试UP")
    ok("videos 共 3 条", len(result.get("videos") or []) == 3)
    ok("stats.total=3", (result.get("stats") or {}).get("total") == 3)
    ok("raw_path 指向 raw_up999999.json",
       str(result.get("raw_path")).endswith("raw_up999999.json"), str(result.get("raw_path")))
    ok("完成后 progress.videos=3", (final.get("progress") or {}).get("videos") == 3)

    status, _h, body = http("GET", lab.base + "/api/job/" + job_id)
    ok("任务结束后能重复查询（幂等）", json.loads(body.decode("utf-8")).get("state") == "done")


# --------------------------------------------------------------------------
# [4] 失败路径
# --------------------------------------------------------------------------


def test_failure() -> None:
    print("\n[4] 失败路径")
    lab = new_lab(crawl_fn=fake_crawl_fn(mid=888888, error=C.CrawlError("搜不到「不存在」")))
    status, _h, body = http("POST", lab.base + "/api/crawl", payload={"query": "不存在"})
    job_id = json.loads(body.decode("utf-8"))["job"]
    final = wait_job(lab, job_id)
    ok("抓取抛 CrawlError 时 state=error", final.get("state") == "error", str(final.get("state")))
    ok("error 保留人话错误信息", "搜不到「不存在」" in (final.get("error") or ""),
       str(final.get("error")))
    ok("失败时 result 为 None", final.get("result") is None)
    ok("失败后错误也进了日志",
       any("搜不到" in line for line in final.get("log") or []))

    status, _h, body = http("GET", lab.base + "/api/job/no-such-job")
    ok("未知 job 返回 404", status == 404, str(status))
    ok("未知 job 返回 ok:false", json.loads(body.decode("utf-8")).get("ok") is False)


# --------------------------------------------------------------------------
# [5] 参数校验
# --------------------------------------------------------------------------


def test_validation() -> None:
    print("\n[5] 参数校验")
    lab = new_lab(crawl_fn=fake_crawl_fn(mid=777777))

    status, _h, body = http("POST", lab.base + "/api/crawl", raw=b"{ this is not json")
    ok("坏 JSON 返回 400", status == 400, str(status))
    ok("坏 JSON 有中文错误", "JSON" in (json.loads(body.decode("utf-8")).get("error") or ""))

    status, _h, _b = http("POST", lab.base + "/api/crawl", payload={})
    ok("缺 query 返回 400", status == 400, str(status))

    status, _h, _b = http("POST", lab.base + "/api/crawl", payload={"query": "   "})
    ok("空白 query 返回 400", status == 400, str(status))

    status, _h, _b = http("POST", lab.base + "/api/crawl", raw=b"")
    ok("空请求体返回 400", status == 400, str(status))

    # 学科校验已放宽成"auto 或已知学科 id"：chemistry 现在是合法内置学科（见 [14] 回归），
    # 这里改用真正不存在的 id 验证 400。
    status, _h, _b = http("POST", lab.base + "/api/crawl",
                          payload={"query": "x", "subject": "bogus"})
    ok("非法 subject 返回 400", status == 400, str(status))

    status, _h, _b = http("POST", lab.base + "/api/crawl",
                          payload={"query": "x", "subject": 123})
    ok("非字符串 subject 返回 400", status == 400, str(status))

    status, _h, _b = http("POST", lab.base + "/api/crawl",
                          payload={"query": ["一数"]})
    ok("非字符串 query 返回 400", status == 400, str(status))

    # 这些校验都不该占用"有任务在跑"的名额
    status, _h, body = http("POST", lab.base + "/api/crawl",
                            payload={"query": "测试UP", "subject": "auto"})
    ok("校验失败不影响后续正常提交", status == 200, str(status))
    ok("正常提交拿到 job", isinstance(json.loads(body.decode("utf-8")).get("job"), str))


# --------------------------------------------------------------------------
# [6] 用户指定学科优先
# --------------------------------------------------------------------------


def test_subject_override() -> None:
    print("\n[6] 用户指定学科优先")
    lab = new_lab(crawl_fn=fake_crawl_fn(mid=666666))
    status, _h, body = http("POST", lab.base + "/api/crawl",
                            payload={"query": "测试UP", "subject": "physics"})
    job_id = json.loads(body.decode("utf-8"))["job"]
    final = wait_job(lab, job_id)
    result = final.get("result") or {}
    info = result.get("info") or {}
    ok("指定 physics 时 info.subject=physics", info.get("subject") == "physics",
       str(info.get("subject")))
    ok("仍然记录自动识别结果 math", info.get("subject_detected") == "math",
       str(info.get("subject_detected")))
    ok("info.subject_label=物理", info.get("subject_label") == "物理")
    ok("result.source.subject 变成物理",
       (result.get("source") or {}).get("subject") == "物理")
    ok("视频挂在 physics 学科命名空间下",
       all(v.get("subject_id") == "physics" for v in (result.get("videos") or [])))
    ok("分类 id 前缀是 physics:",
       all(str(c.get("id", "")).startswith("physics:") for c in (result.get("categories") or [])))


# --------------------------------------------------------------------------
# [7] 落盘与重建（含坏文件容错）
# --------------------------------------------------------------------------


def test_persist_and_rebuild() -> None:
    print("\n[7] 落盘、清单与重建（坏文件容错）")
    tmp = Path(tempfile.mkdtemp(prefix="lab-persist-"))
    (tmp / "raw_broken.json").write_text("{ 这不是合法 JSON", encoding="utf-8")
    lab = new_lab(crawl_fn=fake_crawl_fn(mid=424242, author="持久UP"), data_dir=tmp)

    status, _h, body = http("POST", lab.base + "/api/crawl", payload={"query": "持久UP"})
    job_id = json.loads(body.decode("utf-8"))["job"]
    final = wait_job(lab, job_id)
    ok("存在损坏 raw 文件时任务仍能完成", final.get("state") == "done",
       str(final.get("state")))

    raw_path = tmp / "raw_up424242.json"
    ok("raw 文件已落盘 data/raw_up424242.json", raw_path.is_file())
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    ok("raw 是合法 JSON 且 schema 正确", raw.get("schema") == C.SCHEMA)
    ok("raw.source.mid 正确", (raw.get("source") or {}).get("mid") == 424242)

    manifest = json.loads((tmp / "sources.json").read_text(encoding="utf-8"))
    ok("sources.json 记录了该 mid 的学科",
       (manifest.get("424242") or {}).get("subject_id") == "math", str(manifest))
    ok("sources.json 记录了展示名",
       (manifest.get("424242") or {}).get("name") == "持久UP")
    ok("sources.json 用字符串 mid 当键", "424242" in manifest)

    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    ok("重建后 resources.json 含新来源",
       any(s.get("id") == "up424242" for s in (data.get("sources") or [])),
       str([s.get("id") for s in (data.get("sources") or [])]))
    ok("重建后视频总数=3", (data.get("stats") or {}).get("total") == 3,
       str((data.get("stats") or {}).get("total")))
    ok("resources.js 也写了", (tmp / "resources.js").is_file())
    ok("坏文件被跳过并写进日志",
       any("跳过损坏" in line for line in (final.get("log") or [])))

    status, _h, body = http("GET", lab.base + "/api/sources")
    payload = json.loads(body.decode("utf-8"))
    ok("/api/sources 返回 200 + ok:true", status == 200 and payload.get("ok") is True)
    mine = [s for s in (payload.get("sources") or []) if s.get("id") == "up424242"]
    ok("/api/sources 里能找到新来源", len(mine) == 1, str(payload.get("sources")))
    ok("/api/sources 字段齐全",
       bool(mine) and all(k in mine[0] for k in ("id", "name", "subject", "count", "courses")))
    ok("/api/sources count=3", bool(mine) and mine[0]["count"] == 3)

    # 内置来源沿用历史文件名，避免同一个 mid 出现两份 raw（会算两遍）
    tmp2 = Path(tempfile.mkdtemp(prefix="lab-legacy-"))
    (tmp2 / "raw_bilibili.json").write_text(
        json.dumps(minimal_raw(14229967, "一数"), ensure_ascii=False), encoding="utf-8")
    lab2 = new_lab(crawl_fn=fake_crawl_fn(mid=14229967, author="mid 14229967"), data_dir=tmp2)
    status, _h, body = http("POST", lab2.base + "/api/crawl", payload={"query": "14229967"})
    final2 = wait_job(lab2, json.loads(body.decode("utf-8"))["job"])
    ok("内置来源（一数）任务完成", final2.get("state") == "done")
    data2 = json.loads((tmp2 / "resources.json").read_text(encoding="utf-8"))
    ok("内置来源重写历史文件而不是新建 raw_yishu.json",
       (tmp2 / "raw_bilibili.json").is_file() and not (tmp2 / "raw_yishu.json").exists())
    ok("同一 mid 不会在数据里出现两次",
       len([s for s in (data2.get("sources") or []) if s.get("id") == "yishu"]) == 1,
       str([s.get("id") for s in (data2.get("sources") or [])]))


# --------------------------------------------------------------------------
# [8] /api/reset
# --------------------------------------------------------------------------


def test_reset() -> None:
    print("\n[8] /api/reset 只删用户来源")
    tmp = Path(tempfile.mkdtemp(prefix="lab-reset-"))
    (tmp / "raw_bilibili.json").write_text(
        json.dumps(minimal_raw(14229967, "一数"), ensure_ascii=False), encoding="utf-8")
    (tmp / "raw_huangfuren.json").write_text(
        json.dumps(minimal_raw(23630128, "黄夫人"), ensure_ascii=False), encoding="utf-8")
    (tmp / "raw_up777.json").write_text(
        json.dumps(minimal_raw(777, "临时UP"), ensure_ascii=False), encoding="utf-8")
    (tmp / "sources.json").write_text(json.dumps({
        "14229967": {"id": "yishu", "name": "一数", "subject_id": "math", "subject": "数学"},
        "23630128": {"id": "huangfuren", "name": "黄夫人",
                     "subject_id": "physics", "subject": "物理"},
        "777": {"id": "up777", "name": "临时UP", "subject_id": "other", "subject": "其他"},
    }, ensure_ascii=False), encoding="utf-8")
    lab = new_lab(crawl_fn=None, data_dir=tmp)

    status, _h, body = http("POST", lab.base + "/api/reset")
    payload = json.loads(body.decode("utf-8"))
    ok("/api/reset 200 + ok:true", status == 200 and payload.get("ok") is True)
    ok("只删掉用户添加的 raw_up777.json",
       payload.get("removed") == ["raw_up777.json"], str(payload.get("removed")))
    ok("保留 raw_bilibili.json", (tmp / "raw_bilibili.json").is_file())
    ok("保留 raw_huangfuren.json", (tmp / "raw_huangfuren.json").is_file())
    ok("用户文件已删除", not (tmp / "raw_up777.json").exists())

    manifest = json.loads((tmp / "sources.json").read_text(encoding="utf-8"))
    ok("清单里的用户来源被清掉", "777" not in manifest, str(list(manifest)))
    ok("清单保留两个内置来源", "14229967" in manifest and "23630128" in manifest)

    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    ids = [s.get("id") for s in (data.get("sources") or [])]
    ok("重建后只剩两个内置来源", sorted(ids) == ["huangfuren", "yishu"], str(ids))

    status, _h, body = http("POST", lab.base + "/api/reset")
    ok("再次 reset 幂等（removed 为空）",
       json.loads(body.decode("utf-8")).get("removed") == [])


# --------------------------------------------------------------------------
# [9] 端口占用探测
# --------------------------------------------------------------------------


def test_port_probe() -> None:
    print("\n[9] 端口占用探测（--port 被占用时自动往后试的依据）")
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    sock.listen(5)
    port = int(sock.getsockname()[1])
    ok("已有监听时探测为占用", S._port_in_use("127.0.0.1", port) is True)
    ok("监听在 0.0.0.0 时也能被探到（内部映射成 127.0.0.1）",
       S._port_in_use("0.0.0.0", port) is True)
    sock.close()
    ok("监听关闭后端口恢复可用", S._port_in_use("127.0.0.1", port) is False)
    ok("端口 0 永远算可用（交给系统分配）", S._port_in_use("127.0.0.1", 0) is False)


def raw_for(mid: int, author: str, titles) -> dict:
    """造一份多视频 raw，供学科/来源相关测试复用。"""
    videos = []
    for i, title in enumerate(titles):
        bvid = "BV%d%02d" % (mid, i)
        videos.append({"bvid": bvid, "title": title, "duration": 600 + i * 10,
                       "pubdate": 1700000000, "cover": "", "views": 0, "danmaku": 0,
                       "url": "https://www.bilibili.com/video/" + bvid})
    return {
        "schema": C.SCHEMA,
        "crawled_at": 1700000000000,
        "source": {"platform": "bilibili", "author": author, "mid": mid,
                   "space": "https://space.bilibili.com/%d" % mid},
        "collections": [{
            "season_id": 1, "title": "合集", "intro": "", "cover": "",
            "total": len(videos), "fetched": len(videos), "truncated": False,
            "videos": videos,
        }],
        "stats": {"collections": 1, "videos": len(videos), "truncated_collections": []},
    }


def write_two_builtin_raws(tmp: Path) -> None:
    """两个内置来源：一数（3 个数学视频）+ 黄夫人（2 个物理视频）。"""
    (tmp / "raw_bilibili.json").write_text(
        json.dumps(raw_for(14229967, "一数", MATH_TITLES), ensure_ascii=False),
        encoding="utf-8")
    (tmp / "raw_huangfuren.json").write_text(
        json.dumps(raw_for(23630128, "黄夫人",
                           ["【圆周运动】线速度", "【牛顿定律】受力分析"]),
                   ensure_ascii=False), encoding="utf-8")


def add_subject(lab: Lab, name: str, subject_id: str | None = None):
    payload = {"name": name}
    if subject_id is not None:
        payload["id"] = subject_id
    status, _h, body = http("POST", lab.base + "/api/subjects", payload=payload)
    return status, json.loads(body.decode("utf-8"))


def get_subject_row(lab: Lab, subject_id: str):
    status, _h, body = http("GET", lab.base + "/api/subjects")
    for row in (json.loads(body.decode("utf-8")).get("subjects") or []):
        if row.get("id") == subject_id:
            return row
    return None


# --------------------------------------------------------------------------
# [10] 学科清单读取与新增
# --------------------------------------------------------------------------


def test_subjects_api() -> None:
    print("\n[10] /api/subjects 读取与新增（幂等 / 校验）")
    tmp = Path(tempfile.mkdtemp(prefix="lab-subjects-"))
    write_two_builtin_raws(tmp)
    lab = new_lab(crawl_fn=None, data_dir=tmp)
    # reset 会保留两个内置 raw 并重建，顺便让 resources.json 落盘
    http("POST", lab.base + "/api/reset")

    status, _h, body = http("GET", lab.base + "/api/subjects")
    payload = json.loads(body.decode("utf-8"))
    ok("GET /api/subjects 200 + ok:true", status == 200 and payload.get("ok") is True)
    subs = payload.get("subjects") or []
    ids = [s.get("id") for s in subs]
    ok("返回全部内置学科（含其他）",
       all(x in ids for x in ["chinese", "math", "english", "physics", "chemistry",
                              "biology", "history", "politics", "geography", "other"]),
       str(ids))
    ok("每项含 id/name/builtin/count/sources/courses",
       all(all(k in s for k in ("id", "name", "builtin", "count", "sources", "courses"))
           for s in subs))
    math = [s for s in subs if s["id"] == "math"][0]
    ok("math 计数=3 来源=1", math["count"] == 3 and math["sources"] == 1, str(math))
    phys = [s for s in subs if s["id"] == "physics"][0]
    ok("physics 计数=2", phys["count"] == 2, str(phys))

    status, p = add_subject(lab, "信息技术")
    ok("POST /api/subjects 200 + builtin:false",
       status == 200 and p["subject"]["builtin"] is False, str(p))
    sid = p["subject"]["id"]
    ok("自动 id 以 custom- 开头", sid.startswith("custom-"), sid)
    ok("返回的是中文名", p["subject"]["name"] == "信息技术")

    # 幂等：同名再添加
    status, p2 = add_subject(lab, "信息技术")
    ok("重复添加同名幂等（id 不变）", p2["subject"]["id"] == sid, str(p2))
    custom = json.loads((tmp / "subjects.json").read_text(encoding="utf-8"))["custom"]
    ok("custom 数组不重复", len(custom) == 1, str(custom))

    # 与内置学科重名
    status, p3 = add_subject(lab, "数学")
    ok("与内置重名返回内置学科（builtin:true）",
       status == 200 and p3["subject"]["builtin"] is True and p3["subject"]["id"] == "math",
       str(p3))
    custom = json.loads((tmp / "subjects.json").read_text(encoding="utf-8"))["custom"]
    ok("与内置重名不写 custom", len(custom) == 1, str(custom))

    status, _h, _b = http("POST", lab.base + "/api/subjects", payload={"name": "   "})
    ok("空名字返回 400", status == 400, str(status))
    status, _h, _b = http("POST", lab.base + "/api/subjects",
                          payload={"name": "新学科", "id": "Bad ID"})
    ok("非法 id 返回 400", status == 400, str(status))
    status, _h, _b = http("POST", lab.base + "/api/subjects",
                          payload={"name": "冲突学科", "id": "math"})
    ok("id 与内置冲突返回 400", status == 400, str(status))
    status, p4 = add_subject(lab, "信息技术2", "it-2")
    ok("合法自定义 id 被采用", status == 200 and p4["subject"]["id"] == "it-2", str(p4))

    row = get_subject_row(lab, sid)
    ok("新增学科出现在 GET 清单里（builtin:false，计数 0）",
       row is not None and row["builtin"] is False and row["count"] == 0, str(row))
    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    data_ids = [s["id"] for s in data["subjects"]]
    ok("重建后 resources.json 的 subjects 含自定义学科", sid in data_ids, str(data_ids))
    ok("重建后 resources.json 仍含全部内置学科",
       all(x in data_ids for x in ("math", "physics", "other")), str(data_ids))


# --------------------------------------------------------------------------
# [11] 删除自定义学科
# --------------------------------------------------------------------------


def test_subject_delete() -> None:
    print("\n[11] DELETE /api/subjects/<id>")
    tmp = Path(tempfile.mkdtemp(prefix="lab-subject-del-"))
    write_two_builtin_raws(tmp)
    lab = new_lab(crawl_fn=None, data_dir=tmp)
    http("POST", lab.base + "/api/reset")

    _s, pa = add_subject(lab, "信息技术")
    _s, pb = add_subject(lab, "通用技术")
    a_id, b_id = pa["subject"]["id"], pb["subject"]["id"]

    status, _h, body = http("DELETE", lab.base + "/api/subjects/math")
    ok("删内置学科返回 400", status == 400, str(status))
    ok("内置错误信息是中文人话",
       "内置学科不能删除" in (json.loads(body.decode("utf-8")).get("error") or ""))

    status, _h, body = http("DELETE", lab.base + "/api/subjects/" + a_id)
    p = json.loads(body.decode("utf-8"))
    ok("删自定义学科 200 + removed 正确",
       status == 200 and p.get("removed") == {"id": a_id, "name": "信息技术"}, str(p))
    custom = json.loads((tmp / "subjects.json").read_text(encoding="utf-8"))["custom"]
    ok("subjects.json 里确实删掉了", all(c["id"] != a_id for c in custom), str(custom))
    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    ok("重建后 resources.json 的 subjects 里也没有它了",
       all(s["id"] != a_id for s in data["subjects"]))

    # 让一数用上 b_id，再删它 → 应该 409
    status, _h, _b = http("POST", lab.base + "/api/source/subject",
                          payload={"id": "yishu", "subject_id": b_id})
    ok("先把一数改成通用技术", status == 200, str(status))
    status, _h, body = http("DELETE", lab.base + "/api/subjects/" + b_id)
    err = json.loads(body.decode("utf-8")).get("error") or ""
    ok("有来源在用返回 409", status == 409, str(status))
    ok("409 错误信息列出还在用的来源名", "一数" in err, err)
    custom = json.loads((tmp / "subjects.json").read_text(encoding="utf-8"))["custom"]
    ok("409 时没有静默改数据（学科还在）", any(c["id"] == b_id for c in custom), str(custom))

    status, _h, _b = http("DELETE", lab.base + "/api/subjects/custom-nope")
    ok("未知自定义学科返回 404", status == 404, str(status))

    status, _h, body = http("POST", lab.base + "/api/subjects/delete", payload={"id": b_id})
    ok("POST /api/subjects/delete 等价（同样 409）", status == 409, str(status))


# --------------------------------------------------------------------------
# [12] 给来源改学科
# --------------------------------------------------------------------------


def test_source_subject() -> None:
    print("\n[12] POST /api/source/subject")
    tmp = Path(tempfile.mkdtemp(prefix="lab-src-subject-"))
    write_two_builtin_raws(tmp)
    lab = new_lab(crawl_fn=None, data_dir=tmp)
    http("POST", lab.base + "/api/reset")
    _s, pa = add_subject(lab, "信息技术")
    a_id = pa["subject"]["id"]

    status, _h, body = http("POST", lab.base + "/api/source/subject",
                            payload={"id": "yishu", "subject_id": a_id})
    p = json.loads(body.decode("utf-8"))
    ok("改内置来源学科 200", status == 200 and p.get("ok") is True, str(p))
    src = p.get("source") or {}
    ok("返回的 source.subject_id/科目正确",
       src.get("subject_id") == a_id and src.get("subject") == "信息技术", str(src))

    manifest = json.loads((tmp / "sources.json").read_text(encoding="utf-8"))
    ok("data/sources.json 已落盘新学科",
       (manifest.get("14229967") or {}).get("subject_id") == a_id, str(manifest.get("14229967")))

    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    ys = [s for s in data["sources"] if s["id"] == "yishu"][0]
    ok("重建后来源行学科跟着变",
       ys["subject_id"] == a_id and ys["subject"] == "信息技术", str(ys))
    ok("该来源视频的 subject_id 全部跟着变",
       all(v["subject_id"] == a_id for v in data["videos"] if v["source_id"] == "yishu"))
    row = [s for s in data["subjects"] if s["id"] == a_id][0]
    ok("自定义学科计数=3", row["count"] == 3, str(row))

    status, _h, _b = http("POST", lab.base + "/api/source/subject",
                          payload={"id": "no-such-source", "subject_id": a_id})
    ok("未知来源返回 404", status == 404, str(status))
    status, _h, _b = http("POST", lab.base + "/api/source/subject",
                          payload={"id": "yishu", "subject_id": "no-such-subject"})
    ok("未知学科返回 400", status == 400, str(status))

    status, _h, _b = http("POST", lab.base + "/api/source/subject",
                          payload={"id": "yishu", "subject_id": "math"})
    ok("改回 math 也允许", status == 200, str(status))
    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    ys = [s for s in data["sources"] if s["id"] == "yishu"][0]
    ok("改回后来源学科=math", ys["subject_id"] == "math", str(ys))
    row = [s for s in data["subjects"] if s["id"] == a_id][0]
    ok("改回后自定义学科计数=0", row["count"] == 0, str(row))


# --------------------------------------------------------------------------
# [13] 删除来源（软删除）
# --------------------------------------------------------------------------


def test_source_delete() -> None:
    print("\n[13] POST /api/source/delete（软删除 + 删到 0）")
    tmp = Path(tempfile.mkdtemp(prefix="lab-src-del-"))
    write_two_builtin_raws(tmp)
    lab = new_lab(crawl_fn=None, data_dir=tmp)
    http("POST", lab.base + "/api/reset")
    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    ok("初始总数=5", (data.get("stats") or {}).get("total") == 5,
       str((data.get("stats") or {}).get("total")))

    status, _h, body = http("POST", lab.base + "/api/source/delete", payload={"id": "yishu"})
    p = json.loads(body.decode("utf-8"))
    ok("删来源 200 + removed 指出来源文件",
       status == 200 and str(p.get("removed")).endswith("raw_bilibili.json"), str(p))
    ok("返回的 trash 路径指向 data/trash/",
       str(p.get("trash")).startswith("data/trash/raw_bilibili.json."), str(p.get("trash")))
    trash_files = list((tmp / "trash").glob("raw_bilibili.json.*"))
    ok("raw 文件进了 data/trash/", len(trash_files) == 1, str(trash_files))
    ok("data/ 下的原 raw 已移走", not (tmp / "raw_bilibili.json").exists())
    manifest = json.loads((tmp / "sources.json").read_text(encoding="utf-8"))
    ok("sources.json 里没有该 mid", "14229967" not in manifest, str(list(manifest)))
    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    ok("重建后不含该来源", all(s["id"] != "yishu" for s in data["sources"]))
    ok("总数相应减少（5→2）", (data.get("stats") or {}).get("total") == 2,
       str((data.get("stats") or {}).get("total")))
    math = [s for s in data["subjects"] if s["id"] == "math"][0]
    ok("数学学科计数变 0", math["count"] == 0, str(math))

    status, _h, _b = http("POST", lab.base + "/api/source/delete", payload={"id": "nope"})
    ok("未知来源返回 404", status == 404, str(status))

    status, _h, _b = http("POST", lab.base + "/api/source/delete",
                          payload={"id": "huangfuren"})
    ok("删到 0 个来源也能成功", status == 200, str(status))
    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    ok("空数据集总数=0", (data.get("stats") or {}).get("total") == 0,
       str((data.get("stats") or {}).get("total")))
    ok("空数据集没有来源", data.get("sources") == [])
    ok("空数据集仍保留内置学科清单", len(data.get("subjects") or []) >= 10,
       str([s["id"] for s in data.get("subjects") or []]))
    ok("trash 里现在有两个文件", len(list((tmp / "trash").iterdir())) == 2,
       str(list((tmp / "trash").iterdir())))


# --------------------------------------------------------------------------
# [14] 自定义学科端到端 + crawl subject 校验
# --------------------------------------------------------------------------


def test_custom_subject_end_to_end() -> None:
    print("\n[14] 自定义学科端到端（crawl + subject_label）+ subject 校验")
    tmp = Path(tempfile.mkdtemp(prefix="lab-custom-e2e-"))
    lab = new_lab(crawl_fn=fake_crawl_fn(mid=555001, author="信息UP",
                                         titles=["Python 入门", "算法基础"],
                                         delay=0.05), data_dir=tmp)
    _s, pa = add_subject(lab, "信息技术")
    a_id = pa["subject"]["id"]

    status, _h, body = http("POST", lab.base + "/api/crawl",
                            payload={"query": "信息UP", "subject": a_id})
    ok("用自定义学科 id 提交抓取 200", status == 200, str(status))
    final = wait_job(lab, json.loads(body.decode("utf-8"))["job"])
    ok("任务完成", final.get("state") == "done", str(final.get("state")))
    result = final.get("result") or {}
    src = result.get("source") or {}
    ok("切片 source.subject 是中文名（不是 id、不是其他）",
       src.get("subject") == "信息技术" and src.get("subject_id") == a_id, str(src))
    ok("切片 subjects 里自定义学科计数=2",
       any(s["id"] == a_id and s["count"] == 2 and s["builtin"] is False
           for s in (result.get("subjects") or [])),
       str([(s["id"], s["count"]) for s in (result.get("subjects") or [])]))

    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    row = [s for s in data["subjects"] if s["id"] == a_id][0]
    ok("重建后 resources.json 里自定义学科计数=2", row["count"] == 2, str(row))

    # subject 校验：内置 chemistry 通过
    status, _h, body = http("POST", lab.base + "/api/crawl",
                            payload={"query": "信息UP", "subject": "chemistry"})
    ok("内置 chemistry 通过校验", status == 200, str(status))
    wait_job(lab, json.loads(body.decode("utf-8"))["job"])
    status, _h, _b = http("POST", lab.base + "/api/crawl",
                          payload={"query": "信息UP", "subject": "bogus"})
    ok("bogus 学科返回 400", status == 400, str(status))
    status, _h, body = http("POST", lab.base + "/api/crawl",
                            payload={"query": "信息UP", "subject": a_id})
    ok("自定义学科 id 通过校验", status == 200, str(status))
    wait_job(lab, json.loads(body.decode("utf-8"))["job"])


# --------------------------------------------------------------------------
# [15] /api/reset 清空自定义学科
# --------------------------------------------------------------------------


def test_reset_clears_subjects() -> None:
    print("\n[15] /api/reset 清空自定义学科并保留两个内置 raw")
    tmp = Path(tempfile.mkdtemp(prefix="lab-reset-subjects-"))
    write_two_builtin_raws(tmp)
    (tmp / "raw_up777.json").write_text(
        json.dumps(raw_for(777, "临时UP", ["【导数】切线"]), ensure_ascii=False),
        encoding="utf-8")
    (tmp / "subjects.json").write_text(
        json.dumps({"custom": [{"id": "custom-abc", "name": "信息技术"}]},
                   ensure_ascii=False), encoding="utf-8")
    lab = new_lab(crawl_fn=None, data_dir=tmp)

    status, _h, body = http("POST", lab.base + "/api/reset")
    p = json.loads(body.decode("utf-8"))
    ok("reset 200 + subjects_cleared:true",
       status == 200 and p.get("subjects_cleared") is True, str(p))
    ok("只删用户 raw（raw_up777.json）", p.get("removed") == ["raw_up777.json"],
       str(p.get("removed")))
    ok("保留 raw_bilibili.json", (tmp / "raw_bilibili.json").is_file())
    ok("保留 raw_huangfuren.json", (tmp / "raw_huangfuren.json").is_file())
    custom = json.loads((tmp / "subjects.json").read_text(encoding="utf-8"))["custom"]
    ok("data/subjects.json 的 custom 被清空", custom == [], str(custom))
    data = json.loads((tmp / "resources.json").read_text(encoding="utf-8"))
    ok("重建后 subjects 里没有自定义学科",
       all(s["builtin"] for s in data["subjects"]), str([s["id"] for s in data["subjects"]]))
    ok("重建后两个内置来源仍在",
       sorted(s["id"] for s in data["sources"]) == ["huangfuren", "yishu"])


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def main() -> int:
    try:
        test_static()
        test_ping()
        test_lifecycle()
        test_failure()
        test_validation()
        test_subject_override()
        test_persist_and_rebuild()
        test_reset()
        test_port_probe()
        test_subjects_api()
        test_subject_delete()
        test_source_subject()
        test_source_delete()
        test_custom_subject_end_to_end()
        test_reset_clears_subjects()
    finally:
        for lab in LABS:
            lab.close()
            shutil.rmtree(lab.data_dir, ignore_errors=True)

    print("\n===== 结果: %d 通过 / %d 失败 =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
