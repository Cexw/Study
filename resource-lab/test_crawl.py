#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crawl_bilibili.py 的离线测试（**全程不联网**）。

覆盖：解析、时长格式化、分页终止、失败降级、重试、--offline-cache 复现、
以及真实产出 data/raw_bilibili.json 的数据自检。

用标准库自带的极简 harness，跑完打印断言总数；退出码 0 = 全通过。
运行：python test_cb.py
"""

import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crawl_bilibili as cb  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DATA_JSON = Path(__file__).resolve().parent / "data" / "raw_bilibili.json"
MID = 14229967

# --------------------------------------------------------------------------
# 极简测试框架
# --------------------------------------------------------------------------

_TESTS = []
_STATS = {"asserts": 0, "passed": 0, "failed": 0}


def test(func):
    _TESTS.append(func)
    return func


def check(condition, label):
    _STATS["asserts"] += 1
    if not condition:
        raise AssertionError(label)


def check_eq(actual, expected, label):
    _STATS["asserts"] += 1
    if actual != expected:
        raise AssertionError("%s —— 期望 %r，实际 %r" % (label, expected, actual))


# --------------------------------------------------------------------------
# 假传输层：让"第几次失败"完全可控，且绝不碰网络
# --------------------------------------------------------------------------


class FakeTransport:
    """
    按 URL 子串路由的假传输层。

    routes: [(url_substring, result), ...]，按顺序匹配；
    result 可以是 dict（当作 200 + JSON），也可以是 Exception 实例（直接抛出）。
    参数 match 取 "season_id=<sid>&sort_reverse=false&page_num=<n>" 这种精确串，
    避免 page_num=2 误命中 page_num=20。
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        for needle, result in self.routes:
            if needle in url:
                if isinstance(result, Exception):
                    raise result
                return 200, json.dumps(result, ensure_ascii=False).encode("utf-8")
        raise AssertionError("假传输层没有匹配到路由: %s" % url)

    def count(self, needle):
        return sum(1 for url in self.calls if needle in url)


class FlakyTransport:
    """前 fail_times 次失败，之后成功；用于验证重试。"""

    def __init__(self, fail_times, payload=None, error=None, status=500):
        self.fail_times = fail_times
        self.payload = payload if payload is not None else {"code": 0, "data": {"ok": 1}}
        self.error = error
        self.status = status
        self.calls = 0

    def get(self, url):
        self.calls += 1
        if self.calls <= self.fail_times:
            if self.error is not None:
                raise self.error
            return self.status, b"server error"
        return 200, json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def no_network_transport():
    """离线场景用它，一旦被调用就说明离线模式偷跑了网络。"""
    def boom(url):
        raise AssertionError("离线模式不应发起网络请求: %s" % url)
    return type("NoNet", (), {"get": staticmethod(boom)})()


# --------------------------------------------------------------------------
# 可复用的原始响应样本
# --------------------------------------------------------------------------


def sample_seasons_list(seasons):
    return {
        "code": 0,
        "message": "0",
        "data": {
            "items_lists": {
                "page": {"page_num": 1, "page_size": 20, "total": len(seasons)},
                "seasons_list": seasons,
                "series_list": [],
            }
        },
    }


def season_entry(season_id, title, total, name=None):
    return {
        "meta": {
            "season_id": season_id,
            "name": name if name is not None else "合集·" + title,
            "title": title,
            "total": total,
            "cover": "http://archive.biliimg.com/bfs/archive/aaa.jpg",
            "description": "简介 &amp; 说明",
        },
        "archives": [],
    }


def sample_archives(count, total=None, page_num=1, page_size=30, start=0, meta_title="合集标题"):
    archives = []
    for index in range(start, start + count):
        archives.append({
            "aid": 1000 + index,
            "bvid": "BV%08d" % index,
            "title": "第%d课 <em class=\"keyword\">函数</em> &amp; 导数" % index,
            "duration": 60 * index,
            "pubdate": 1700000000 + index,
            "pic": "http://i0.hdslb.com/bfs/archive/bbb.jpg",
            "stat": {"view": 100 + index, "danmaku": index},
        })
    return {
        "code": 0,
        "message": "0",
        "data": {
            "archives": archives,
            "meta": {"total": total if total is not None else count, "cover": "",
                     "description": "合集简介", "title": meta_title},
            "page": {"page_num": page_num, "page_size": page_size,
                     "total": total if total is not None else count},
        },
    }


# ==========================================================================
# 1. 解析函数
# ==========================================================================


@test
def test_parse_seasons_list():
    raw = sample_seasons_list([
        season_entry(1533671, "高中数学大合集", 188),
        season_entry(8397438, "新高考一轮复习", 11, name="合集·新高考 一轮 &amp; 二轮"),
    ])
    seasons, page = cb.extract_seasons(raw)

    check_eq(len(seasons), 2, "合集条数")
    check_eq(seasons[0]["season_id"], 1533671, "season_id")
    check_eq(seasons[0]["title"], "高中数学大合集", "标题取 meta.title")
    check_eq(seasons[0]["total"], 188, "total")
    check_eq(seasons[1]["title"], "新高考一轮复习", "标题仍取 meta.title")

    # name 里带 "合集·" 前缀时必须被剥掉（只对 name 用）
    check_eq(cb.clean_collection_name("合集·高中数学大合集"), "高中数学大合集", "剥掉 合集· 前缀")
    check_eq(cb.clean_collection_name("合集"), "合集", "没有中点前缀就不动")
    # 回归保护：真叫「合集标题」的标题绝不能被剥成「标题」
    check_eq(cb.clean_title("合集标题"), "合集标题", "meta.title 不做前缀剥离")
    check_eq(cb.clean_title("合集整理"), "合集整理", "meta.title 不做前缀剥离(2)")
    # 没有 title 字段时回落到 name
    check_eq(cb.parse_season_meta({"meta": {"season_id": 1, "name": "合集·X", "total": 2}})["title"],
             "X", "无 title 时回落 name 并剥前缀")
    check_eq(page["total"], 2, "分页信息")

    # 封面 http -> https
    check(seasons[0]["cover"].startswith("https://"), "封面升级到 https")
    # 简介里的实体被还原
    check_eq(seasons[0]["intro"], "简介 & 说明", "简介 unescape")


@test
def test_parse_archives_and_html():
    raw = sample_archives(2, total=2)
    videos, total, page_size = cb.extract_archives(raw)

    check_eq(len(videos), 2, "视频条数")
    check_eq(total, 2, "合集总集数")
    check_eq(page_size, 30, "接口页大小")

    first = videos[0]
    check_eq(first["bvid"], "BV00000000", "bvid")
    check_eq(first["title"], "第0课 函数 & 导数", "剥 <em> 且 unescape 实体")
    check_eq(first["duration"], 0, "duration 秒")
    check_eq(first["pubdate"], 1700000000, "pubdate")
    check_eq(first["views"], 100, "views")
    check_eq(first["danmaku"], 0, "danmaku")
    check_eq(first["url"], "https://www.bilibili.com/video/BV00000000", "url 按 bvid 拼")
    check(first["cover"].startswith("https://"), "封面升级到 https")

    # 边界：title 里的 HTML 实体与标签
    check_eq(cb.clean_text('<em class="keyword">函数</em> &amp; 导数 &#39;q&#39; &quot;x&quot;'),
             '函数 & 导数 \'q\' "x"', "实体 + 标签混合")
    check_eq(cb.clean_text("&lt;em&gt;不该被当成标签&lt;/em&gt;"), "<em>不该被当成标签</em>",
             "转义过的标签不能被误删（先剥标签再 unescape）")
    check_eq(cb.clean_text(None), "", "None 安全")
    check_eq(cb.clean_text("  a  "), "a", "去首尾空白")

    # 合集详情接口的 meta 给出更干净的标题
    detail = cb.extract_season_meta_from_archives(raw)
    check_eq(detail["title"], "合集标题", "从 archives.meta 取标题")


# ==========================================================================
# 2. 时长格式化
# ==========================================================================


@test
def test_format_duration():
    cases = [
        (0, "00:00"),
        (59, "00:59"),
        (60, "01:00"),
        (3599, "59:59"),
        (3600, "01:00:00"),
        (3661, "01:01:01"),
        (409643, "113:47:23"),
    ]
    for seconds, expected in cases:
        check_eq(cb.format_duration(seconds), expected, "format_duration(%s)" % seconds)

    check_eq(cb.format_duration(None), "00:00", "None -> 00:00")
    check_eq(cb.format_duration(-5), "00:00", "负数 -> 00:00")
    check_eq(cb.format_duration("90"), "01:30", "字符串数字也吃")


# ==========================================================================
# 3. 分页终止条件（且不会多请求一页）
# ==========================================================================


@test
def test_pagination_termination():
    # (a) 第一页就是空数组 -> 只请求 1 次
    calls = []

    def empty_fetch(page_num):
        calls.append(page_num)
        return [], 0
    videos, truncated = cb.collect_season_archives(empty_fetch, page_size=30)
    check_eq(calls, [1], "空数组只请求 1 页")
    check_eq(videos, [], "空数组无视频")
    check_eq(truncated, False, "total=0 的空页不算截断")

    # (b) 达到 total 就停 -> 不请求第 2 页
    calls = []

    def exact_fetch(page_num):
        calls.append(page_num)
        return cb.extract_archives(sample_archives(3, total=3, page_num=page_num, page_size=3))[0], 3
    videos, truncated = cb.collect_season_archives(exact_fetch, page_size=3)
    check_eq(calls, [1], "达到 total 后不再翻页")
    check_eq(len(videos), 3, "抓满 3 条")
    check_eq(truncated, False, "刚好抓满不算截断")

    # (c) 短页 = 最后一页 -> 停
    calls = []

    def short_fetch(page_num):
        calls.append(page_num)
        count = 3 if page_num == 1 else 2
        start = 0 if page_num == 1 else 3
        data = cb.extract_archives(sample_archives(count, total=5, page_num=page_num, page_size=3, start=start))
        return data[0], 5
    videos, truncated = cb.collect_season_archives(short_fetch, page_size=3)
    check_eq(calls, [1, 2], "短页终止，共 2 次请求")
    check_eq(len(videos), 5, "两页合计 5 条")
    check_eq(truncated, False, "正常抓完不算截断")

    # (d) 满页 + total 未知 -> 必须再翻一页，空页后停
    calls = []

    def unknown_total_fetch(page_num):
        calls.append(page_num)
        if page_num == 1:
            start = 0
            data = cb.extract_archives(sample_archives(2, total=2, page_num=1, page_size=2, start=start))
            return data[0], 0
        return [], 0
    videos, truncated = cb.collect_season_archives(unknown_total_fetch, page_size=2)
    check_eq(calls, [1, 2], "total 未知时翻到空页为止")
    check_eq(len(videos), 2, "保留第 1 页数据")

    # (e) 接口说还有集数却给空页 -> 截断
    def lying_fetch(page_num):
        if page_num == 1:
            return cb.extract_archives(sample_archives(4, total=10, page_num=1, page_size=4))[0], 10
        return [], 10
    videos, truncated = cb.collect_season_archives(lying_fetch, page_size=4)
    check_eq(len(videos), 4, "保留已抓数据")
    check_eq(truncated, True, "接口总数未达成 -> 截断")

    # (f) max_items 上限
    def full_fetch(page_num):
        return cb.extract_archives(sample_archives(10, total=100, page_num=page_num, page_size=10))[0], 100
    videos, truncated = cb.collect_season_archives(full_fetch, page_size=10, max_items=4)
    check_eq(len(videos), 4, "max_items 生效")
    check_eq(truncated, True, "被上限截断 -> truncated=True")


# ==========================================================================
# 4. 降级：第 2 页失败要保留第 1 页数据并继续下一个合集
# ==========================================================================


@test
def test_degradation_keeps_data_and_continues():
    sid_a, sid_b = 111, 222
    routes = [
        ("seasons_series_list", sample_seasons_list([
            season_entry(sid_a, "合集A", 45),
            season_entry(sid_b, "合集B", 5),
        ])),
        ("season_id=111&sort_reverse=false&page_num=1",
         sample_archives(30, total=45, page_num=1, meta_title="合集A")),
        ("season_id=111&sort_reverse=false&page_num=2", cb.CrawlError("模拟第 2 页 412")),
        ("season_id=222&sort_reverse=false&page_num=1",
         sample_archives(5, total=5, page_num=1, meta_title="合集B")),
    ]
    transport = FakeTransport(routes)
    crawler = cb.Crawler(mid=MID, transport=transport, sleep=0, retries=1,
                         cache_dir=None, log=lambda *a: None)
    collections, truncated = crawler.crawl()

    check_eq(len(collections), 2, "两个合集都被处理")
    a, b = collections

    check_eq(a["truncated"], True, "合集A 标记截断")
    check_eq(a["fetched"], 30, "合集A 保留第 1 页的 30 条")
    check_eq(len(a["videos"]), 30, "合集A videos 仍在")
    check_eq(a["total"], 45, "合集A 记录接口总数")
    check_eq(a["videos"][0]["bvid"], "BV00000000", "第 1 页内容正确")

    check_eq(b["truncated"], False, "合集B 不受影响")
    check_eq(b["fetched"], 5, "合集B 抓到 5 条")
    check_eq(b["videos"][0]["bvid"], "BV00000000", "合集B 数据正确")

    check_eq(truncated, ["合集A"], "truncated_collections 记账")

    # 降级后不应继续无意义地请求合集A 的第 3 页
    check_eq(transport.count("season_id=111"), 2, "合集A 只请求了 2 页就放弃")

    # 整份输出仍然合法
    output = cb.build_output(MID, "一数", collections, truncated, crawled_at=1)
    check_eq(output["stats"]["videos"], 35, "统计视频数")
    check_eq(output["stats"]["collections"], 2, "统计合集数")
    check_eq(output["stats"]["truncated_collections"], ["合集A"], "统计截断合集")
    check_eq(output["schema"], "resource-lab/bilibili-collections/v1", "schema 正确")


# ==========================================================================
# 5. 重试
# ==========================================================================


@test
def test_retry_logic():
    # 前两次 500，第三次成功 -> 总共调用 3 次
    flaky = FlakyTransport(fail_times=2, status=500)
    result = cb.fetch_json("https://example.invalid/x", retries=3, sleep=0, transport=flaky)
    check_eq(flaky.calls, 3, "失败两次后成功，共 3 次调用")
    check_eq(result["code"], 0, "最终拿到结果")

    # 412 也要重试
    flaky_412 = FlakyTransport(fail_times=2, status=412)
    cb.fetch_json("https://example.invalid/x", retries=3, sleep=0, transport=flaky_412)
    check_eq(flaky_412.calls, 3, "412 也会重试")

    # 传输层直接抛异常（超时）也要重试
    flaky_exc = FlakyTransport(fail_times=2, error=TimeoutError("timed out"))
    cb.fetch_json("https://example.invalid/x", retries=3, sleep=0, transport=flaky_exc)
    check_eq(flaky_exc.calls, 3, "超时异常也会重试")

    # 一直失败 -> 抛 CrawlError，且调用次数 = retries
    always = FlakyTransport(fail_times=99, status=503)
    try:
        cb.fetch_json("https://example.invalid/x", retries=3, sleep=0, transport=always)
        raise AssertionError("一直失败却没有抛 CrawlError")
    except cb.CrawlError:
        _STATS["asserts"] += 1
    check_eq(always.calls, 3, "重试耗尽，共 3 次调用")

    # 不可重试的业务错误只请求一次，且是 CrawlError 的子类
    fatal = FlakyTransport(fail_times=-1, payload={"code": -404, "message": "啥都木有"})
    try:
        cb.fetch_json("https://example.invalid/x", retries=3, sleep=0, transport=fatal)
        raise AssertionError("业务错误没有抛出")
    except cb.FatalCrawlError:
        _STATS["asserts"] += 1
    except cb.CrawlError:
        raise AssertionError("应该是 FatalCrawlError")
    check_eq(fatal.calls, 1, "不可重试的业务错误只请求 1 次")

    # 重试成功时也应该把原始响应交给 save_raw（缓存不能丢）
    saved = []
    flaky_save = FlakyTransport(fail_times=1)
    cb.fetch_json("https://example.invalid/x", retries=3, sleep=0, transport=flaky_save,
                  save_raw=saved.append)
    check_eq(len(saved), 1, "成功后写入缓存 1 次")


# ==========================================================================
# 6. --offline-cache：只用缓存目录里的原始响应生成输出
# ==========================================================================


@test
def test_offline_cache_path():
    tmp = tempfile.mkdtemp(prefix="bili_offline_")
    try:
        cache = Path(tmp)
        sid = 1533671

        # 两个假缓存文件：合集列表 + 某个合集的第 1 页
        (cache / cb.seasons_list_cache_path(cache, 14229967, 1, 20).name).write_text(
            json.dumps(sample_seasons_list([season_entry(sid, "高中数学大合集", 2)]),
                       ensure_ascii=False), encoding="utf-8")
        (cache / cb.season_cache_path(cache, sid, 1, 30).name).write_text(
            json.dumps(sample_archives(2, total=2, page_num=1), ensure_ascii=False),
            encoding="utf-8")

        crawler = cb.Crawler(mid=14229967, transport=no_network_transport(), sleep=0,
                             cache_dir=cache, offline=True, log=lambda *a: None)
        collections, truncated = crawler.crawl()
        output = cb.build_output(14229967, "一数", collections, truncated, crawled_at=123)

        check_eq(crawler.http_count, 0, "离线模式 0 次 HTTP")
        check_eq(len(collections), 1, "从缓存解析出 1 个合集")
        check_eq(collections[0]["season_id"], sid, "season_id 来自缓存")
        check_eq(collections[0]["fetched"], 2, "抓到 2 条")
        check_eq(collections[0]["truncated"], False, "不算截断")

        # schema 校验
        check_eq(output["schema"], "resource-lab/bilibili-collections/v1", "schema")
        check_eq(set(output.keys()), {"schema", "crawled_at", "source", "collections", "stats"},
                 "顶层字段齐全")
        check_eq(output["source"]["platform"], "bilibili", "platform")
        check_eq(output["source"]["mid"], 14229967, "mid")
        check_eq(output["stats"]["videos"], 2, "stats.videos")
        check_eq(output["stats"]["collections"], 1, "stats.collections")
        video = output["collections"][0]["videos"][0]
        check_eq(set(video.keys()),
                 {"bvid", "title", "duration", "pubdate", "cover", "views", "danmaku", "url"},
                 "视频字段齐全")

        # 缓存缺失时要抛 CrawlError，而不是偷偷联网
        empty = Path(tempfile.mkdtemp(prefix="bili_empty_"))
        try:
            missing = cb.Crawler(mid=14229967, transport=no_network_transport(), sleep=0,
                                 cache_dir=empty, offline=True, log=lambda *a: None)
            try:
                missing.crawl()
                raise AssertionError("缓存缺失却没有抛 CrawlError")
            except cb.CrawlError:
                _STATS["asserts"] += 1
        finally:
            shutil.rmtree(empty, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ==========================================================================
# 7. 真实产出数据自检
# ==========================================================================


@test
def test_generated_data_is_valid():
    check(DATA_JSON.exists(), "data/raw_bilibili.json 必须存在（先跑一次联网抓取）")
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))

    check_eq(data["schema"], "resource-lab/bilibili-collections/v1", "schema")
    check_eq(data["source"]["platform"], "bilibili", "platform")
    check_eq(data["source"]["mid"], 14229967, "mid")
    check_eq(data["source"]["author"], "一数", "author")
    check_eq(data["source"]["space"], "https://space.bilibili.com/14229967", "space")
    check(isinstance(data["crawled_at"], int) and data["crawled_at"] > 1_600_000_000_000,
          "crawled_at 是毫秒时间戳")

    collections = data["collections"]
    check_eq(data["stats"]["collections"], len(collections), "stats.collections 与数组一致")

    total_videos = sum(len(c["videos"]) for c in collections)
    check_eq(data["stats"]["videos"], total_videos, "stats.videos == sum(len(videos))")
    check_eq(data["stats"]["truncated_collections"], [], "本次抓取没有截断合集")

    seen_bvids = set()
    for collection in collections:
        for key in ("season_id", "title", "intro", "cover", "total", "fetched",
                    "truncated", "videos"):
            check(key in collection, "合集字段 %s 存在" % key)
        check_eq(collection["fetched"], len(collection["videos"]),
                 "fetched == len(videos) @ %s" % collection["title"])
        check(isinstance(collection["season_id"], int), "season_id 是整数")
        check(isinstance(collection["total"], int), "total 是整数")

        for video in collection["videos"]:
            for key in ("bvid", "title", "duration", "pubdate", "cover", "views",
                        "danmaku", "url"):
                check(key in video, "视频字段 %s 存在" % key)
            check(video["bvid"].startswith("BV"), "bvid 格式: %s" % video["bvid"])
            check(video["bvid"] not in seen_bvids, "bvid 唯一: %s" % video["bvid"])
            seen_bvids.add(video["bvid"])
            check_eq(video["url"], "https://www.bilibili.com/video/%s" % video["bvid"],
                     "url 与 bvid 匹配")
            check(isinstance(video["duration"], int) and video["duration"] >= 0,
                  "duration 是非负整数: %r" % video["duration"])
            check(isinstance(video["views"], int) and video["views"] >= 0, "views 非负整数")
            check(isinstance(video["danmaku"], int) and video["danmaku"] >= 0, "danmaku 非负整数")
            check(isinstance(video["pubdate"], int) and video["pubdate"] > 0, "pubdate 是正整数")
            check(video["cover"].startswith("https://"), "封面是 https")
            check("<em" not in video["title"], "标题不含残留 <em> 标签")
            check("&amp;" not in video["title"] and "&quot;" not in video["title"],
                  "标题不含未还原实体")

    check(len(data["collections"]) == 7, "实测有 7 个合集")
    check(total_videos > 0, "至少抓到一些视频")
    check(isinstance(cb.format_duration(409643), str), "格式时长可用")


# --------------------------------------------------------------------------
# 「输入 UP 名/ID 自动抓取」用到的解析与搜索兜底（全部用假传输层，不联网）
# --------------------------------------------------------------------------


def search_item(mid, author, bvid, title, duration="12:34", play=1000, pubdate=1700000000,
                pic="//i0.hdslb.com/bfs/x.jpg"):
    return {"mid": mid, "author": author, "bvid": bvid, "title": title, "duration": duration,
            "play": play, "pubdate": pubdate, "pic": pic, "video_review": 7}


def search_payload(items):
    return {"code": 0, "data": {"result": items}}


def card_payload(name, fans=12345):
    return {"code": 0, "data": {"card": {"name": name}, "follower": fans}}


@test
def test_query_parsing_helpers():
    """mid 提取 / 时长字符串 / 封面链接归一。"""
    check_eq(cb.extract_mid_from_query("14229967"), 14229967, "纯数字当 mid")
    check_eq(cb.extract_mid_from_query("space.bilibili.com/23630128"), 23630128, "主页短链")
    check_eq(cb.extract_mid_from_query("https://space.bilibili.com/999?tab=archive"), 999,
             "带参数主页链接")
    check_eq(cb.extract_mid_from_query("一数"), None, "名字提取不到 mid")
    check_eq(cb.extract_mid_from_query("  "), None, "空串提取不到 mid")

    check_eq(cb.parse_search_duration("2:32"), 152, "MM:SS")
    check_eq(cb.parse_search_duration("1:02:03"), 3723, "HH:MM:SS")
    check_eq(cb.parse_search_duration("90"), 90, "纯秒数")
    check_eq(cb.parse_search_duration(""), 0, "空串当 0")
    check_eq(cb.parse_search_duration("坏"), 0, "非法值当 0")
    check_eq(cb.parse_search_duration(None), 0, "None 当 0")

    check_eq(cb.normalize_cover("//i0.hdslb.com/a.jpg"), "https://i0.hdslb.com/a.jpg",
             "协议相对链接补 https")
    check_eq(cb.normalize_cover("http://i0.hdslb.com/b.jpg"), "https://i0.hdslb.com/b.jpg",
             "http 升级为 https")
    check_eq(cb.normalize_cover(""), "", "空封面保持空")
    check_eq(cb.normalize_cover(None), "", "None 封面当空")


@test
def test_resolve_up_by_mid_gets_real_name():
    """按 ID 解析时要用卡片接口补真实昵称，否则搜索兜底会拿 "mid N" 去搜。"""
    tr = FakeTransport([("web-interface/card", card_payload("某老师", 8888))])
    info = cb.resolve_up("998877", transport=tr)
    check_eq(info["mid"], 998877, "mid 解析")
    check_eq(info["author"], "某老师", "昵称来自卡片接口")
    check_eq(info["via"], "mid", "via 标记")
    check_eq(info["fans"], 8888, "粉丝数")
    check_eq(tr.count("web-interface/card"), 1, "卡片接口只请求一次")

    # 卡片接口挂掉 / 返回非 0 code 时，不能把整个解析搞崩
    tr2 = FakeTransport([("web-interface/card", cb.CrawlError("接口繁忙"))])
    info2 = cb.resolve_up("998877", transport=tr2)
    check_eq(info2["mid"], 998877, "取昵称失败仍然拿到 mid")
    check_eq(info2["author"], "mid 998877", "退化成占位名")
    tr3 = FakeTransport([("web-interface/card", {"code": -799, "message": "请求过于频繁"})])
    check_eq(cb.resolve_up("998877", transport=tr3)["author"], "mid 998877", "非 0 code 也退化")


@test
def test_resolve_up_by_name_prefers_exact_match():
    """同名优先：完全同名 > 出现次数最多。"""
    items = ([search_item(111, "一数", "BV%d" % i, "高一数学 %d" % i) for i in range(5)]
             + [search_item(222, "一数老师", "BVx%d" % i, "别的 %d" % i) for i in range(9)])
    tr = FakeTransport([("search/type", search_payload(items))])
    info = cb.resolve_up("一数", transport=tr)
    check_eq(info["mid"], 111, "完全同名优先（哪怕出现次数少）")
    check_eq(info["via"], "search-exact", "标记为精确匹配")
    check(info["candidates"] and info["candidates"][0]["hits"] >= 5, "候选里给出命中次数")

    # 没有完全同名时退化成"出现次数最多的作者"
    tr2 = FakeTransport([("search/type", search_payload(items))])
    info2 = cb.resolve_up("数", transport=tr2)
    check_eq(info2["mid"], 222, "没有同名就选出现最多的")
    check_eq(info2["via"], "search", "标记为模糊匹配")


@test
def test_resolve_up_errors():
    """搜不到就给人话错误，而不是抛底层异常。"""
    tr = FakeTransport([("search/type", search_payload([]))])
    try:
        cb.resolve_up("肯定不存在的老师名字", transport=tr)
        check(False, "应该抛 CrawlError")
    except cb.CrawlError as exc:
        check("搜不到" in str(exc), "错误信息是人话：%s" % exc)
    try:
        cb.resolve_up("", transport=tr)
        check(False, "空输入也应该抛 CrawlError")
    except cb.CrawlError:
        check(True, "空输入抛 CrawlError")


@test
def test_collect_by_search_fallback():
    """没有合集的 UP 主：搜索兜底只留该作者、去重、标 truncated。"""
    page1 = ([search_item(555, "某老师", "BV%d" % i, "第 %d 课 导数" % i) for i in range(29)]
             + [search_item(666, "别的老师", "BVother", "别人的视频")])
    page2 = [search_item(555, "某老师", "BVlast", "最后一课", duration="1:00:00"),
             search_item(555, "某老师", "BV0", "重复的 BV0 应被去重")]
    tr = FakeTransport([("&page=1&", search_payload(page1)), ("&page=2&", search_payload(page2))])
    cols = cb.collect_by_search("某老师", 555, transport=tr)
    check_eq(len(cols), 1, "拼成一个合集")
    col = cols[0]
    check_eq(col["title"], "搜索结果", "合集名")
    check(col["truncated"] is True, "搜索兜底天然不完整，必须标 truncated")
    check_eq(len(col["videos"]), 30, "29 + 1，别人的视频和重复 bvid 都被剔除")
    check(all(v["bvid"].startswith("BV") for v in col["videos"]), "bvid 都在")
    check_eq(col["videos"][0]["duration"], 754, "时长字符串 12:34 被转成 754 秒")
    check_eq(col["videos"][-1]["duration"], 3600, "1:00:00 → 3600")
    check_eq(col["videos"][0]["url"], "https://www.bilibili.com/video/BV0", "url 由 bvid 拼出")
    check_eq(col["videos"][0]["cover"], "https://i0.hdslb.com/bfs/x.jpg", "封面升级为 https")
    check_eq(tr.count("search/type"), 2, "短页之后不再多请求一页")

    # 一条都搜不到 → 返回空列表，让上层报"没有合集也没搜到"
    tr_empty = FakeTransport([("search/type", search_payload([]))])
    check_eq(cb.collect_by_search("某老师", 555, transport=tr_empty), [], "搜不到就返回空")


# --------------------------------------------------------------------------
# 运行
# --------------------------------------------------------------------------


def run():
    print("=" * 64)
    print("  离线测试 test_cb.py（不联网）")
    print("=" * 64)
    for func in _TESTS:
        try:
            func()
            _STATS["passed"] += 1
            print("  [通过] %s" % func.__name__)
        except Exception as exc:
            _STATS["failed"] += 1
            print("  [失败] %s" % func.__name__)
            print("         %s: %s" % (type(exc).__name__, exc))
            traceback.print_exc()
    print("-" * 64)
    print("  用例：%d 通过 / %d 失败（共 %d）"
          % (_STATS["passed"], _STATS["failed"], len(_TESTS)))
    print("  断言：%d" % _STATS["asserts"])
    print("=" * 64)
    return 0 if _STATS["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
