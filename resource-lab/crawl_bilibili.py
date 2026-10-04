#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B 站 UP 主课程合集爬虫（纯 Python 标准库，无第三方依赖）。

抓取 UP 主「一数」(mid=14229967) 的全部「合集」(season) 及其中的视频，
输出结构化 JSON 给网站使用。

为什么必须准备 cookie？
    B 站的 polymer 接口在没有 cookie 时会被风控拦截返回 HTTP 412（实测
    /x/space/wbi/arc/search 就是 412，所以本项目完全不用那个接口）。办法不是
    登录，而是伪造一个"有指纹的普通浏览器"：先 GET 一次首页让服务端下发
    buvid3 / b_nut，再 GET /x/frontend/finger/spi 拿 b_3 / b_4，把它们以
    buvid3 / buvid4 的名字塞回**同一个** CookieJar，之后所有请求复用它。
    少了这一步，带中文参数的查询会直接 412。

为什么降级时必须保留已抓到的数据？
    最大的合集有 188 集、要 7 次分页请求，任何一次抖动都可能让整轮白跑。
    所以约定"就地降级"：某一页失败只放弃这一页，保留前面已拿到的视频，把该
    合集标记 truncated=true 并记进 stats.truncated_collections，然后**继续抓
    下一个合集**。宁可少几集，也不能丢一整个合集或中断整轮。

缓存与离线：
    --cache-dir 把每个接口的原始响应按语义化文件名落盘；--offline-cache 只读
    缓存、完全不联网，用于离线复跑与测试。
"""

import argparse
import gzip
import html
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCHEMA = "resource-lab/bilibili-collections/v1"
DEFAULT_MID = 14229967
DEFAULT_AUTHOR = "一数"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
HOME_URL = "https://www.bilibili.com/"
SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
SEASONS_LIST_URL = ("https://api.bilibili.com/x/polymer/web-space/seasons_series_list"
                    "?mid={mid}&page_num={page_num}&page_size={page_size}")
SEASONS_ARCHIVES_URL = ("https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
                        "?mid={mid}&season_id={season_id}&sort_reverse=false"
                        "&page_num={page_num}&page_size={page_size}")
MAX_PAGES = 200  # 防御性死循环保险；正常靠 total / 空数组 / 短页终止
RETRYABLE_HTTP = {408, 412, 429}          # 这些状态码要重试，>=500 也重试
RETRYABLE_BIZ_CODE = {-412, -429, -509}   # B 站把风控写在 JSON code 里（-412=被拦截）


def _setup_console():
    """Windows 控制台默认 GBK，中文输出会炸；统一改成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # 流被重定向/不支持 reconfigure 时安静跳过
            pass


_setup_console()


class CrawlError(Exception):
    """抓取失败（可重试次数耗尽，或传输层直接报错）。"""


class FatalCrawlError(CrawlError):
    """明确的业务错误（如合集不存在），重试没有意义。"""


# --------------------------------------------------------------------------
# 文本与数值清洗
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(value):
    """
    清洗标题/简介。

    顺序很关键：**先剥标签、再 unescape**。反过来会把标题里本来就写着的
    "&lt;em&gt;" 还原成 "<em>" 再当标签删掉，导致文字丢失。
    """
    if not value:
        return ""
    return html.unescape(_TAG_RE.sub("", str(value))).strip()


def clean_title(value):
    """标题清洗：只处理实体和标签，**绝不动内容本身**。"""
    return clean_text(value)


def clean_collection_name(value):
    """
    合集名清洗：去掉 B 站给 meta.name 自动加的 "合集·" 前缀。

    只对 meta.name 用！meta.title 本来就是干净标题，对它剥前缀会把一个真叫
    「合集整理」的合集错改成「整理」。
    """
    text = clean_text(value)
    if text.startswith("合集·"):
        text = text[len("合集·"):]
    return text.strip()


def normalize_url(url):
    """B 站很多封面返回 http://，网站是 https，会被浏览器当混合内容拦掉。"""
    if not url:
        return ""
    url = str(url).strip()
    return "https://" + url[7:] if url.startswith("http://") else url


def to_int(value, default=0):
    """接口偶尔把数字给成字符串，统一成 int；失败就给默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def format_duration(seconds):
    """秒数 -> "MM:SS"（不足 1 小时）或 "HH:MM:SS"。小时不取模，409643 -> 113:47:23。"""
    total = max(0, to_int(seconds, 0))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return "%02d:%02d:%02d" % (hours, minutes, secs) if hours else "%02d:%02d" % (minutes, secs)


def video_url(bvid):
    return "https://www.bilibili.com/video/%s" % bvid


def pad_display(text, width):
    """按终端显示宽度左对齐补齐：中文算 2 列，ASCII 算 1 列。"""
    shown = sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(text))
    return str(text) + " " * max(0, width - shown)


# --------------------------------------------------------------------------
# 传输层与请求（含重试）
# --------------------------------------------------------------------------


def request_headers():
    """所有请求共用的头；Referer 必须指向该 UP 主空间，否则容易被风控。"""
    return {
        "User-Agent": USER_AGENT,
        "Referer": "https://space.bilibili.com/%d" % DEFAULT_MID,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


def _read(opener, url, timeout):
    req = urllib.request.Request(url, headers=request_headers())
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read()
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            body = gzip.decompress(body)
        return getattr(resp, "status", 200), body


def build_cookie_jar_opener(timeout=20.0):
    """
    走完"首页 -> spi"两步，返回一个已经带上 buvid3/buvid4 的 opener 和它的 jar。
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    # 第 1 步：首页会 Set-Cookie: buvid3 / b_nut，CookieProcessor 自动收下
    _read(opener, HOME_URL, timeout)
    # 第 2 步：spi 把 b_3 / b_4 放在 JSON 里，只能手工写进同一个 jar
    _status, body = _read(opener, SPI_URL, timeout)
    data = (json.loads(body.decode("utf-8")) or {}).get("data") or {}
    for name, value in (("buvid3", data.get("b_3")), ("buvid4", data.get("b_4"))):
        if not value:
            continue
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=".bilibili.com", domain_specified=True, domain_initial_dot=True,
            path="/", path_specified=True, secure=False, expires=None,
            discard=False, comment=None, comment_url=None, rest={}, rfc2109=False))
    return opener, jar


class HttpTransport:
    """默认传输层：用带 cookie 的 opener 发请求，返回 (状态码, 原始字节)。"""

    def __init__(self, opener, timeout=20.0):
        self._opener = opener
        self._timeout = timeout

    def get(self, url):
        return _read(self._opener, url, self._timeout)


_shared_transport = None


def get_shared_transport():
    """惰性构建全局传输层；只有真联网时才会走到这里。"""
    global _shared_transport
    if _shared_transport is None:
        _shared_transport = HttpTransport(build_cookie_jar_opener()[0])
    return _shared_transport


def fetch_json(url, retries=3, sleep=0.6, transport=None, save_raw=None):
    """
    请求一个接口并解析 JSON，指数退避重试。

    transport 只要求实现 get(url) -> (status, body_bytes)，抛 CrawlError 也算失败；
    测试靠注入假 transport 来控制"第几次失败"。重试耗尽后抛 CrawlError，交给上层降级。
    """
    transport = transport if transport is not None else get_shared_transport()
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            status, body = transport.get(url)
            if status != 200 or status in RETRYABLE_HTTP or status >= 500:
                raise CrawlError("HTTP %s" % status)
            data = json.loads(body.decode("utf-8"))
            code = data.get("code")
            if code:
                message = ("%s %s" % (code, data.get("message") or "")).strip()
                if code in RETRYABLE_BIZ_CODE:
                    raise CrawlError("接口返回 code=%s" % message)
                raise FatalCrawlError("接口返回 code=%s" % message)
            if save_raw is not None:
                save_raw(body)
            return data
        except FatalCrawlError:
            raise
        except CrawlError as exc:
            last_error = exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # URLError 含超时/DNS/TLS，HTTPError(412) 是它的子类，ValueError 含 JSON 解析失败
            last_error = CrawlError("%s: %s" % (type(exc).__name__, exc))
        if attempt < retries:
            time.sleep(sleep * (2 ** (attempt - 1)))  # 指数退避 0.6 / 1.2 / 2.4 ...

    raise CrawlError("请求失败（已重试 %d 次）: %s —— 最后错误: %s" % (retries, url, last_error))


# --------------------------------------------------------------------------
# 缓存文件命名（测试也会 import 这两个函数来构造假缓存）
# --------------------------------------------------------------------------


def seasons_list_cache_path(cache_dir, mid, page_num, page_size):
    return Path(cache_dir) / ("seasons_list_m%d_ps%d_p%d.json" % (mid, page_size, page_num))


def season_cache_path(cache_dir, season_id, page_num, page_size):
    return Path(cache_dir) / ("season_%d_ps%d_p%d.json" % (season_id, page_size, page_num))


# --------------------------------------------------------------------------
# 解析：接口原始响应 -> 内部结构
# --------------------------------------------------------------------------


def parse_season_meta(entry):
    """从 seasons_series_list 的一项抽出合集信息。"""
    meta = entry.get("meta") or entry
    # meta.name 形如 "合集·高中数学大合集"，meta.title 才是干净的 "高中数学大合集"
    return {
        "season_id": to_int(meta.get("season_id")),
        "title": clean_title(meta.get("title")) or clean_collection_name(meta.get("name")),
        "intro": clean_text(meta.get("description")),
        "cover": normalize_url(meta.get("cover")),
        "total": to_int(meta.get("total")),
    }


def extract_seasons(data):
    """返回 (合集列表, 分页信息)。"""
    items = (data.get("data") or {}).get("items_lists") or {}
    raw = items.get("seasons_list") or []
    return [parse_season_meta(entry) for entry in raw], (items.get("page") or {})


def parse_archive(item):
    """从 seasons_archives_list 的一条视频抽出字段。"""
    stat = item.get("stat") or {}
    bvid = str(item.get("bvid") or "")
    return {
        "bvid": bvid,
        "title": clean_text(item.get("title")),
        "duration": to_int(item.get("duration")),
        "pubdate": to_int(item.get("pubdate")),
        "cover": normalize_url(item.get("pic")),
        "views": to_int(stat.get("view")),
        "danmaku": to_int(stat.get("danmaku")),
        "url": video_url(bvid),
    }


def extract_archives(data):
    """返回 (视频列表, 合集总集数, 接口页大小)。"""
    payload = data.get("data") or {}
    archives = [parse_archive(item) for item in (payload.get("archives") or [])]
    meta, page = payload.get("meta") or {}, payload.get("page") or {}
    total = to_int(meta.get("total")) or to_int(page.get("total"))
    return archives, total, to_int(page.get("page_size"))


def extract_season_meta_from_archives(data):
    """合集详情接口的 data.meta 也能给出标题/简介/封面，且标题更干净。"""
    meta = ((data.get("data") or {}).get("meta")) or {}
    if not meta:
        return {}
    return {
        "title": clean_title(meta.get("title")) or clean_collection_name(meta.get("name")),
        "intro": clean_text(meta.get("description")),
        "cover": normalize_url(meta.get("cover")),
        "total": to_int(meta.get("total")),
    }


# --------------------------------------------------------------------------
# 分页：只依赖"页函数"，方便离线/假数据测试终止条件
# --------------------------------------------------------------------------


def collect_season_archives(fetch_page, page_size=30, max_items=0, on_error=None):
    """
    逐页拉取一个合集的视频。fetch_page(page_num) -> (archives_list, total)。

    终止条件（满足其一即停，且**不会多请求一页**）：返回空数组 / 累计达到 total /
    本页条数少于 page_size（说明是最后一页）。
    任何一页抛 CrawlError 就地降级：保留已抓到的，truncated=True，正常返回。
    返回 (videos, truncated)。
    """
    videos, truncated, page_num = [], False, 1

    while page_num <= MAX_PAGES:
        try:
            archives, total = fetch_page(page_num)
        except CrawlError as exc:
            truncated = True  # 这一页没拿到，本合集标记为不完整
            if on_error is not None:
                on_error(page_num, exc)
            break

        if not archives:
            truncated = bool(total) and len(videos) < total  # 接口说还有，却给空页
            break

        videos.extend(archives)

        if max_items and len(videos) >= max_items:
            videos = videos[:max_items]
            truncated = (not total) or (len(videos) < total)
            break
        if total and len(videos) >= total:
            break
        if len(archives) < page_size:
            break
        page_num += 1

    return videos, truncated


# --------------------------------------------------------------------------
# 爬虫主体
# --------------------------------------------------------------------------


class Crawler:
    def __init__(self, mid=DEFAULT_MID, author=DEFAULT_AUTHOR, transport=None,
                 sleep=0.6, retries=3, cache_dir=None, offline=False,
                 max_per_collection=0, season_page_size=20, archives_page_size=30,
                 log=print):
        self.mid = mid
        self.author = author
        self.transport = transport
        self.sleep = sleep
        self.retries = retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.offline = offline
        self.max_per_collection = max_per_collection
        self.season_page_size = season_page_size
        self.archives_page_size = archives_page_size
        self.log = log
        self.http_count = 0  # 统计真实请求次数，用于报告

    def _get(self, url, cache_path):
        """联网取数并把原始响应落盘；离线模式只读缓存，不联网。"""
        if self.offline:
            if cache_path is None or not Path(cache_path).exists():
                raise CrawlError("离线模式缺少缓存文件: %s" % cache_path)
            return json.loads(Path(cache_path).read_text(encoding="utf-8"))

        save_raw = None
        if self.cache_dir is not None:
            path = Path(cache_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            save_raw = lambda body, _p=path: _p.write_bytes(body)  # noqa: E731

        self.http_count += 1
        return fetch_json(url, retries=self.retries, sleep=self.sleep,
                          transport=self.transport, save_raw=save_raw)

    def fetch_seasons(self):
        """抓全集列表，翻页直到没有新合集。"""
        seasons, seen, page_num = [], set(), 1

        while page_num <= MAX_PAGES:
            url = SEASONS_LIST_URL.format(mid=self.mid, page_num=page_num,
                                          page_size=self.season_page_size)
            cache_path = seasons_list_cache_path(self.cache_dir or ".", self.mid,
                                                 page_num, self.season_page_size)
            try:
                data = self._get(url, cache_path)
            except CrawlError as exc:
                if page_num == 1:
                    raise  # 第一页就失败 = 完全没有数据，交给上层报错
                self.log("  ! 合集列表第 %d 页失败，停止翻页：%s" % (page_num, exc))
                break

            metas, _page = extract_seasons(data)
            if not metas:
                break
            fresh = [m for m in metas if m["season_id"] not in seen]
            seen.update(m["season_id"] for m in fresh)
            seasons.extend(fresh)

            # 注意：items_lists.page.total 会连 series_list 一起数（实测 total=10
            # 但只有 7 个合集），不能用它判终止；用"没有新合集"/"短页"更可靠。
            if not fresh or len(metas) < self.season_page_size:
                break
            page_num += 1

        return seasons

    def fetch_one_season(self, meta):
        """抓单个合集的全部视频，遇到失败就地降级。"""
        season_id = meta["season_id"]
        state = {"total": meta["total"], "meta": {}}

        def fetch_page(page_num):
            url = SEASONS_ARCHIVES_URL.format(mid=self.mid, season_id=season_id,
                                              page_num=page_num,
                                              page_size=self.archives_page_size)
            cache_path = season_cache_path(self.cache_dir or ".", season_id,
                                           page_num, self.archives_page_size)
            data = self._get(url, cache_path)
            videos, total, _ps = extract_archives(data)
            if total:
                state["total"] = total
            state["meta"] = extract_season_meta_from_archives(data) or state["meta"]
            return videos, state["total"]

        def on_error(page_num, exc):
            self.log("  ! [%s] 第 %d 页失败，保留已抓数据：%s" % (meta["title"], page_num, exc))

        videos, truncated = collect_season_archives(
            fetch_page, page_size=self.archives_page_size,
            max_items=self.max_per_collection, on_error=on_error)

        detail = state["meta"] or {}
        total = state["total"] or meta["total"] or len(videos)
        if not truncated and total and len(videos) < total:
            truncated = True  # 没报错但总数对不上，同样算没抓全

        return {
            "season_id": season_id,
            "title": detail.get("title") or meta["title"],
            "intro": detail.get("intro") or meta["intro"] or "",
            "cover": detail.get("cover") or meta["cover"] or "",
            "total": total,
            "fetched": len(videos),
            "truncated": bool(truncated),
            "videos": videos,
        }

    def crawl(self):
        if self.offline:
            self.log("离线模式：只读缓存目录，不联网。")
        seasons = self.fetch_seasons()
        self.log("发现 %d 个合集，开始抓取视频……" % len(seasons))

        collections, truncated = [], []
        for index, meta in enumerate(seasons, 1):
            collection = self.fetch_one_season(meta)
            collections.append(collection)
            if collection["truncated"]:
                truncated.append(collection["title"])
            self.log("  [%d/%d] %s —— %d/%d%s"
                     % (index, len(seasons), collection["title"], collection["fetched"],
                        collection["total"], "（截断）" if collection["truncated"] else ""))
        return collections, truncated


def build_output(mid, author, collections, truncated_titles, crawled_at=None):
    """按网站约定的 schema 组装最终 JSON。"""
    return {
        "schema": SCHEMA,
        "crawled_at": int(time.time() * 1000) if crawled_at is None else crawled_at,
        "source": {
            "platform": "bilibili",
            "author": author,
            "mid": mid,
            "space": "https://space.bilibili.com/%d" % mid,
        },
        "collections": collections,
        "stats": {
            "collections": len(collections),
            "videos": sum(len(c["videos"]) for c in collections),
            # 存标题而不是 season_id：这是一块给人看的摘要，需要 id 的话
            # collections[].season_id 就在旁边。
            "truncated_collections": list(truncated_titles),
        },
    }


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


# ==========================================================================
# 「输入 UP 主名称或 ID 自动抓取」用到的解析与兜底
# ==========================================================================
SEARCH_URL = ("https://api.bilibili.com/x/web-interface/search/type"
              "?search_type=video&keyword={kw}&page={page}&page_size=30")
USER_CARD_URL = "https://api.bilibili.com/x/web-interface/card?mid={mid}"


def fetch_up_name(mid, transport=None, log=None):
    """按 mid 取 UP 主真实昵称与粉丝数，返回 (name, fans)。

    名字只是"锦上添花"：拿不到就返回 (None, 0)，绝不因此让整个解析失败
    （B 站可能返回 -799 请求过于频繁）。
    """
    try:
        data = fetch_json(USER_CARD_URL.format(mid=int(mid)), transport=transport, retries=2)
    except CrawlError as exc:
        if log:
            log("  ! 取昵称失败（不影响抓取）：%s" % exc)
        return None, 0
    if to_int(data.get("code")) != 0:
        return None, 0
    d = data.get("data") or {}
    card = d.get("card") or {}
    return (card.get("name") or d.get("name") or None), to_int(d.get("follower") or card.get("fans"))


def parse_search_duration(value):
    """搜索接口的 duration 是 "12:34" / "1:02:03" 这种字符串，转成秒。"""
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    try:
        nums = [int(p) for p in text.split(":")]
    except ValueError:
        return 0
    sec = 0
    for n in nums:
        sec = sec * 60 + n
    return sec


def normalize_cover(url):
    """B 站封面有时给 //i0.hdslb.com/...，补协议；顺便 http→https（否则 https 页面里是混合内容）。"""
    u = str(url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("http://"):
        u = "https://" + u[7:]
    return u


def extract_mid_from_query(query):
    """从 "14229967" / "space.bilibili.com/14229967" / 主页链接里取 mid；取不到返回 None。"""
    text = str(query or "").strip()
    m = re.search(r"space\.bilibili\.com/(\d+)", text)
    if m:
        return int(m.group(1))
    if text.isdigit():
        return int(text)
    return None


def resolve_up(query, transport=None, log=None):
    """把用户输入（UP 名 / mid / 主页链接）解析成 mid + 作者名。

    返回 {"mid", "author", "via", "candidates"}；实在解析不到就抛 CrawlError。
    名字解析走搜索接口：优先"完全同名"，否则取出现次数最多的作者，
    并把前几名候选一起返回，方便界面提示"你要找的是不是这个"。
    """
    log = log or (lambda *a: None)
    text = str(query or "").strip()
    if not text:
        raise CrawlError("请输入 UP 主名称或 ID")

    mid = extract_mid_from_query(text)
    if mid is not None:
        log("按 ID 解析：mid=%d" % mid)
        # 用卡片接口补上真实昵称：否则 author 会退化成 "mid 123456"，
        # 而"没有合集的 UP 主"要靠作者名去搜索兜底，用 "mid 123456" 搜是搜不到的。
        name, fans = fetch_up_name(mid, transport=transport, log=log)
        if name:
            log("  昵称：%s（粉丝 %s）" % (name, fans))
        return {"mid": mid, "author": name or ("mid %d" % mid), "via": "mid",
                "fans": fans, "candidates": []}

    url = SEARCH_URL.format(kw=urllib.parse.quote(text), page=1)
    data = fetch_json(url, transport=transport)
    result = ((data.get("data") or {}).get("result")) or []
    if not result:
        raise CrawlError("搜不到「%s」相关的视频，换个名字，或直接用 UP 主 ID" % text)

    counts = {}
    for item in result:
        author, m = item.get("author"), to_int(item.get("mid"))
        if author and m:
            counts[(author, m)] = counts.get((author, m), 0) + 1
    if not counts:
        raise CrawlError("搜索没返回可用的 UP 主信息")

    target = text.lower().replace(" ", "")
    exact = [k for k in counts if k[0].lower().replace(" ", "") == target]
    if exact:
        author, mid = max(exact, key=lambda k: counts[k])
        via = "search-exact"
    else:
        (author, mid), _ = max(counts.items(), key=lambda kv: kv[1])
        via = "search"
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    log("按名字解析：%s → mid=%d（%s）" % (text, mid, author))
    return {"mid": int(mid), "author": author, "via": via,
            "candidates": [{"author": a, "mid": m, "hits": n} for (a, m), n in top]}


def collect_by_search(author, mid, transport=None, log=None, max_pages=10, sleep=0.6):
    """有些 UP 主没有公开合集，这时用搜索接口兜底：以作者名为关键词搜，只留该作者的视频。

    注意：这不是完整投稿列表（搜索只覆盖能被这个关键词命中的内容），所以拼出来的合集
    会带 truncated=True，界面上也会说明"不保证完整"。
    """
    log = log or (lambda *a: None)
    videos, seen = [], set()
    for page in range(1, max_pages + 1):
        url = SEARCH_URL.format(kw=urllib.parse.quote(str(author)), page=page)
        try:
            data = fetch_json(url, transport=transport, sleep=sleep)
        except CrawlError as exc:
            log("  ! 搜索第 %d 页失败：%s" % (page, exc))
            break
        result = ((data.get("data") or {}).get("result")) or []
        if not result:
            break
        added = 0
        for item in result:
            if to_int(item.get("mid")) != int(mid):
                continue
            bvid = str(item.get("bvid") or "").strip()
            if not bvid or bvid in seen:
                continue
            seen.add(bvid)
            added += 1
            videos.append({
                "bvid": bvid,
                "title": clean_title(item.get("title")),
                "duration": parse_search_duration(item.get("duration")),
                "pubdate": to_int(item.get("pubdate")),
                "cover": normalize_cover(item.get("pic")),
                "views": to_int(item.get("play")),
                "danmaku": to_int(item.get("video_review")),
                "url": video_url(bvid),
            })
        log("  搜索第 %d 页：新增 %d 个（累计 %d）" % (page, added, len(videos)))
        if len(result) < 30:
            break
        time.sleep(max(0.0, sleep))
    if not videos:
        return []
    return [{
        "season_id": "search",
        "title": "搜索结果",
        "intro": "这个 UP 主没有公开合集；这里是按名字用搜索接口抓到的视频，不保证完整。",
        "cover": videos[0]["cover"],
        "total": len(videos),
        "fetched": len(videos),
        "truncated": True,
        "videos": videos,
    }]


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="抓取 B 站 UP 主「一数」的课程合集与视频，输出结构化 JSON。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n  python crawl_bilibili.py\n"
               "  python crawl_bilibili.py --max-per-collection 5\n"
               "  python crawl_bilibili.py --offline-cache\n")
    parser.add_argument("--mid", type=int, default=DEFAULT_MID, help="UP 主 mid（默认 14229967）")
    parser.add_argument("--author", default=DEFAULT_AUTHOR, help="UP 主名称（默认 一数）")
    parser.add_argument("--out", default="data/raw_bilibili.json", help="输出 JSON 路径")
    parser.add_argument("--cache-dir", default="data/cache", help="原始响应缓存目录")
    parser.add_argument("--offline-cache", action="store_true",
                        help="只用缓存目录里的原始响应重新解析，不联网")
    parser.add_argument("--sleep", type=float, default=0.6, help="请求间隔秒数（默认 0.6）")
    parser.add_argument("--max-per-collection", type=int, default=0,
                        help="每个合集最多抓多少集，0=不限（调试用）")
    parser.add_argument("--retries", type=int, default=3, help="单次请求最大尝试次数（默认 3）")
    parser.add_argument("--timeout", type=float, default=20.0, help="单次请求超时秒数")
    return parser


def print_summary(output, elapsed, http_count, offline):
    stats = output["stats"]
    print("\n" + "=" * 64)
    print("  抓取完成%s" % ("（离线缓存复现）" if offline else ""))
    print("=" * 64)
    print("  schema      : %s" % output["schema"])
    print("  UP 主       : %s (mid=%d)" % (output["source"]["author"], output["source"]["mid"]))
    print("  合集数      : %d" % stats["collections"])
    print("  视频总数    : %d" % stats["videos"])
    print("  截断合集    : %s" % ("无" if not stats["truncated_collections"]
                                  else "、".join(stats["truncated_collections"])))
    if not offline:
        print("  HTTP 请求数 : %d" % http_count)
    print("-" * 64)
    for index, collection in enumerate(output["collections"], 1):
        print("  [%2d] %s %4d/%-4d 集%s"
              % (index, pad_display(collection["title"], 26), collection["fetched"],
                 collection["total"], "  截断" if collection["truncated"] else ""))
    print("-" * 64)
    print("  耗时        : %.2f 秒" % elapsed)
    print("=" * 64)


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    started = time.time()

    transport = None
    if not args.offline_cache:
        # 只有联网模式才需要准备 cookie（离线模式完全不碰网络）
        try:
            opener, jar = build_cookie_jar_opener(timeout=args.timeout)
            transport = HttpTransport(opener, timeout=args.timeout)
            print("cookie 准备完成，当前 cookie: %s"
                  % ", ".join(sorted(c.name for c in jar)))
        except Exception as exc:
            print("cookie 准备失败：%s" % exc, file=sys.stderr)
            return 1

    crawler = Crawler(
        mid=args.mid, author=args.author, transport=transport, sleep=args.sleep,
        retries=args.retries, cache_dir=args.cache_dir, offline=args.offline_cache,
        max_per_collection=args.max_per_collection)

    try:
        collections, truncated = crawler.crawl()
    except CrawlError as exc:
        print("抓取失败：%s" % exc, file=sys.stderr)
        return 1

    output = build_output(args.mid, args.author, collections, truncated)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print_summary(output, time.time() - started, crawler.http_count, args.offline_cache)
    print("已写入：%s" % out_path.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
