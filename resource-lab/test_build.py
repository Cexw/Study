#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_resources.py 的测试：python test_build.py

覆盖两套学科的分类规则、方括号优先逻辑、标签提取、时长格式化、单课/课程包区分、
多来源合并、脏数据容错、输出文件，以及（如果已经爬过）真实数据的自洽性检查。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_resources as B  # noqa: E402

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


print("\n[1] 数学分类规则（一数）")
MATH_CASES = [
    ("【初高衔接】上高中前，你需要知道的“等式变形”技巧都在这！", "chugao"),
    ("【初高衔接】奇形怪状的“不等式”怎么解？一个视频搞定！", "chugao"),   # 优先级：初高衔接 > 不等式
    ("高中数学基础与解法全集（涵盖所有）|从零开始拯救所有学渣", "jichu"),
    ("【集合】交并补运算的三种考法", "jihe"),
    ("【导数】切线问题还在丢分？三种题型一次讲透", "hanshu"),
    ("【三角函数】解三角形必考题型：边角互化", "sanjiao"),               # 三角必须先于函数
    ("【数列】错位相减求和，看完这题就会了", "shulie"),
    ("不等式的放缩技巧怎么想", "budengshi"),
    ("【复数】复数运算易错点", "xiangliang"),
    ("【立体几何】外接球半径公式，一节课讲明白", "liti"),
    ("【圆锥曲线】离心率求值范围问题", "jiexi"),
    ("【概率统计】排列组合12种模型全梳理", "gailv"),
    ("压轴题的常见构造套路", "jiqiao"),
    ("一轮复习怎么安排？三轮复习时间表", "gaokao"),
    ("【学习方法】数学从60到120，我做对了这几件事", "xuexi"),
    ("【答疑】作业太多写不完怎么办？", "xuexi"),
    ("【资料】高中数学公式手册，打印版在这", "ziliao"),
    ("这是一个完全看不出科目的话题", "qita"),
    ("", "qita"),
]
bad = [(t[:24], want, B.classify(t, "math")[0]) for t, want in MATH_CASES
       if B.classify(t, "math")[0] != want]
ok("数学代表标题都归到预期分类（%d 例）" % len(MATH_CASES), not bad, str(bad[:4]))

print("\n[2] 物理分类规则（黄夫人）")
PHY_CASES = [
    ("【运动的描述】质点", "yundong"),
    ("27.【匀变速直线运动】打点计时器-纸带分析", "yundong"),
    ("13.【高中物理必修一】【力学】力的分解情况", "li"),
    ("3.【高中物理必修一】【力学】摩擦力方向判断", "li"),
    ("45.【高中物理必修一】【传送带】传送带题目总结", "niudun"),
    ("63.【高中物理必修二】【平抛运动】带角度平抛", "quxian"),
    ("【圆周运动】线速度与角速度", "quxian"),          # 曲线运动要先于运动学，否则被"速度"抢走
    ("100.【高中物理必修二】【功与能】功的概念", "gongneng"),
    ("152.【高中物理选修3-1】【电场】库仑定律三点共线", "jingdianchang"),
    ("227.【高中物理选修3-1】【安培力】等效长度", "cichang"),
    ("209.【高中物理选修3-1】【高中电路】△U比△I问题", "dianlu"),
    ("【选修3-2电磁感应】【自感现象2】30.自感练习讲解", "dianci"),
    ("【选修3-2交流电】【四值】3.瞬时值和最大值", "jiaoliu"),
    ("【选修3-3】【理想气体】23.压强分析例题", "re"),
    ("【选修3-5】【第四章】23.半衰期", "jindai"),
    ("【简谐运动】16.单摆等效摆长", "zhenbo"),
    ("【机械波】3.波的传播方向", "zhenbo"),
    ("【光】5.海市蜃楼与沙漠蜃景", "guang"),
    ("【物理试卷】2025广东卷选择题", "shijuan"),
    ("【补充】1.指引介绍", "buchong"),
    ("一段完全看不出来的话题", "qita"),
]
bad = [(t[:24], want, B.classify(t, "physics")[0]) for t, want in PHY_CASES
       if B.classify(t, "physics")[0] != want]
ok("物理代表标题都归到预期分类（%d 例）" % len(PHY_CASES), not bad, str(bad[:4]))
ok("同一标题在不同学科下可能分到不同类（学科隔离生效）",
   B.classify("【导数】切线问题", "math")[0] != B.classify("【导数】切线问题", "physics")[0]
   or B.classify("【导数】切线问题", "physics")[0] == "qita")
ok("未知学科退化为其他", B.classify("【导数】切线", "chemistry")[0] == "qita")

print("\n[2b] 化学分类规则（一化儿）")
CHEM_CASES = [
    ("【物质的分类】高一还没学偏偏就考你！", "qita"),
    ("【初升高衔接】化学必修一：离子反应|课后刷刷题", "lzfy"),
    ("【知识点串讲】必修一 离子反应重点梳理", "lzfy"),
    ("氧化剂与还原剂-高一阶段复习|知识点梳理+精选例题", "lzfy"),
    ("学习高中化学前必须要会的化合价判断", "lzfy"),
    ("【题刷刷】高一11月阶段强化：阿伏加德罗常数题型", "jl"),
    ("【高考必刷1200题】物质的量及其相关物理量", "jl"),
    ("【高考必刷1200题】以物质的量为中心的计量", "jl"),
    ("【有机化学】酯的水解反应", "yhx"),
    ("【2024高考冲刺】限制条件的同分异构体书写（下）", "yhx"),
    ("【高考系统课】有机化学基础：酯", "yhx"),
    ("【高考冲刺】电化学题型-最后一课", "hxfy"),          # 知识点胜过"冲刺"
    ("【高考总复习】盐类水解基本概念+水解平衡常数", "hxfy"),
    ("【选择性必修一】化学反应的方向——经典习题讲解", "hxfy"),
    ("【高考冲刺】考前回归教材-重要方程式梳理(元素及其化合物篇)", "yshh"),
    ("【元素化合物】钠及其化合物性质", "yshh"),
    ("【选择性必修二】原子结构及核外电子排布（上）", "wljg"),
    ("高中化学【杂化轨道理论】一头雾水？看这就懂啦！", "wljg"),
    ("【选择性必修二】VSEPR模型与分子空间结构", "wljg"),
    ("【实验】酸碱中和滴定误差分析", "hxsy"),
    ("【2024高考冲刺】考前回归教材", "fxyy"),              # 没有知识点信号 → 复习类
    ("一段完全看不出来的话", "qita"),
]
bad_c = [(t[:24], want, B.classify(t, "chemistry")[0]) for t, want in CHEM_CASES
         if B.classify(t, "chemistry")[0] != want]
ok("化学代表标题都归到预期分类（%d 例）" % len(CHEM_CASES), not bad_c, str(bad_c[:4]))
ok("化学的方括号里出现「高考冲刺」这类阶段名时，知识点仍然赢",
   B.classify("【高考冲刺】电化学题型-最后一课", "chemistry")[0] == "hxfy"
   and B.classify("【高考总复习】盐类水解基本概念", "chemistry")[0] == "hxfy")
ok("阶段类按名称跳过（各学科 id 不同也不漏）：化学 fxyy 也能被跳过",
   "复习与应试" in B.BRACKET_SKIP_NAMES and "fxyy" not in B.BRACKET_SKIP)
ok("化学规则里不再有'复习与应试'排在知识点前面",
   [c[0] for c in B.SUBJECT_RULES["chemistry"]][-1] == "fxyy")
ok("化学核心概念都独立成类（离子反应/氧化还原、物质的量）",
   any(c[0] == "lzfy" for c in B.SUBJECT_RULES["chemistry"])
   and any(c[0] == "jl" for c in B.SUBJECT_RULES["chemistry"]))

print("\n[3] 「方括号优先」规则")
ok("【一轮复习】导数专题 → 函数与导数（而不是高考复习）",
   B.classify("【一轮复习】导数专题训练", "math")[0] == "hanshu",
   B.classify("【一轮复习】导数专题训练", "math")[1])
ok("【补充】牛二解释惯性 → 牛顿（而不是补充专题）",
   B.classify("【补充】15.牛二解释惯性", "physics")[0] == "niudun",
   B.classify("【补充】15.牛二解释惯性", "physics")[1])
ok("【选修3-2电磁感应】【动能定理1】 → 电磁感应（方括号章节胜过正文关键词）",
   B.classify("【选修3-2电磁感应】【动能定理1】27.安培力功能关系", "physics")[0] == "dianci",
   B.classify("【选修3-2电磁感应】【动能定理1】27.安培力功能关系", "physics")[1])
ok("【资料】… 不参与方括号匹配，回退到整标题（仍然是资料）",
   B.classify("【资料】高中数学公式手册", "math")[0] == "ziliao")
ok("带序号前缀的【】也认（27.【匀变速…】）",
   B.classify("27.【匀变速直线运动】竖直上抛", "physics")[0] == "yundong")
ok("「」括号也认", B.classify("「直线和圆」最值模型大梳理", "math")[0] == "jiexi")
ok("数字前缀 + 中文顿号也认",
   B.classify("3、 【机械波】波的干涉", "physics")[0] == "zhenbo")
ok("没有方括号时按整标题匹配",
   B.classify("电场的叠加原理讲解", "physics")[0] == "jingdianchang")

print("\n[4] 标签提取")
ok("抓到【方括号】里的系列名",
   B.extract_tags("【初高衔接】不等式怎么解", ["不等式"])[:1] == ["初高衔接"])
ok("中括号与「」也能抓",
   B.extract_tags("[合集·高中数学] 序章", []) == ["合集·高中数学"]
   and B.extract_tags("「直线和圆」模型", [])[0] == "直线和圆")
ok("标签不超过 4 个",
   len(B.extract_tags("【A】【B】【C】【D】【E】函数导数不等式数列", ["函数", "导数", "不等式"])) <= 4)
ok("多来源下标签仍然稳定（物理案例）",
   "电磁感应" in B.extract_tags("【选修3-2电磁感应】【自感现象2】30.自感练习讲解", ["电磁感应", "自感"]))
ok("空标题安全", B.extract_tags("", []) == [])

print("\n[5] 时长格式化与课程包")
clock_cases = [(0, "0:00"), (59, "0:59"), (60, "1:00"), (3599, "59:59"), (3600, "1:00:00"),
               (3661, "1:01:01"), (409643, "113:47:23")]
ok("时长格式（含边界）", all(B.clock(s) == want for s, want in clock_cases),
   " ".join("%d→%s" % (s, B.clock(s)) for s, _ in clock_cases))
ok("负数/None 视为 0", B.clock(-5) == "0:00" and B.clock(None) == "0:00")
hum = [(0, "0 秒"), (59, "59 秒"), (60, "1 分钟"), (3660, "1 小时 1 分"), (3600, "1 小时"),
       (7200, "2 小时")]
ok("人话时长", all(B.human_duration(s) == want for s, want in hum))
ok("时长角标：普通视频用 MM:SS / HH:MM:SS",
   B.duration_label(1863) == "31:03" and B.duration_label(3661) == "1:01:01")
ok("时长角标：课程包用小时", B.duration_label(409643) == "114 小时" and B.duration_label(10800) == "3 小时")
ok("课程包阈值就在 3 小时", B.duration_label(10799) != "3 小时" and B.duration_label(10800) == "3 小时")
ok("BUNDLE_SECONDS = 3 小时", B.BUNDLE_SECONDS == 10800)

print("\n[6] 单来源：原始数据 → 网站数据")
RAW_MATH = {
    "source": {"platform": "bilibili", "author": "一数", "mid": 14229967,
               "space": "https://space.bilibili.com/14229967"},
    "collections": [
        {"season_id": 111, "title": "高中数学大合集", "intro": "长期更新", "cover": "c1.jpg",
         "total": 3, "fetched": 3, "truncated": False,
         "videos": [
             {"bvid": "BV1", "title": "【导数】切线问题", "duration": 1863, "pubdate": 1700000000,
              "cover": "v1.jpg", "views": 4210000, "danmaku": 21000},
             {"bvid": "BV2", "title": "【数列】错位相减", "duration": 1220, "pubdate": 1700100000,
              "cover": "v2.jpg", "views": 5110000, "danmaku": 25000},
             {"bvid": "BV3", "title": "", "duration": 100, "pubdate": 1700200000},   # 空标题应被跳过
         ]},
        {"season_id": 222, "title": "高考最后十课", "intro": "", "cover": "",
         "total": 1, "fetched": 1, "truncated": False,
         "videos": [{"bvid": "BV4", "title": "【最后十课】考试心态", "duration": 1680,
                     "pubdate": 1700300000, "views": 5210000}]},
    ],
}
data = B.build([RAW_MATH], now=1700500000.0)
ok("schema 是 v3", data["schema"] == "resource-lab/resources/v3")
ok("顶层字段齐全",
   all(k in data for k in ("schema", "generated_at", "generated_at_text", "sources", "stats",
                           "categories", "courses", "videos")))
ok("sources 里有学科信息",
   data["sources"][0]["name"] == "一数" and data["sources"][0]["subject_id"] == "math"
   and data["sources"][0]["subject"] == "数学")
ok("空标题被跳过（3+1 条只留 3 条）", data["stats"]["total"] == 3)
ok("每条视频都带来源与学科字段",
   all(v["source_id"] == "yishu" and v["author"] == "一数" and v["subject"] == "数学"
       for v in data["videos"]))
ok("课程 id / 分类 id 都带命名空间前缀",
   all(":" in v["course_id"] and ":" in v["category_id"] for v in data["videos"]),
   data["videos"][0]["course_id"] + " / " + data["videos"][0]["category_id"])
ok("url 由 bvid 自动补全", data["videos"][0]["url"] == "https://www.bilibili.com/video/BV1")
ok("已有 url 优先保留",
   B.build([{"collections": [{"season_id": 1, "title": "t", "videos": [
       {"bvid": "BVx", "title": "标题", "url": "https://example.com/x"}]}]}])["videos"][0]["url"]
   == "https://example.com/x")
ok("order 从 0 连续递增", [v["order"] for v in data["videos"]] == [0, 1, 2])
ok("date_text 由 pubdate 生成", data["videos"][0]["date_text"] == "2023-11-15")
ok("分类计数之和 = 视频总数",
   sum(c["count"] for c in data["categories"]) == data["stats"]["total"])
ok("分类按学科 + 数量排序",
   all(data["categories"][i]["subject_id"] == "math" for i in range(len(data["categories"]))))
ok("课程计数正确", data["courses"][0]["count"] == 2 and data["courses"][1]["count"] == 1)
ok("课程时长 = 其视频时长之和", data["courses"][0]["duration_sec"] == 1863 + 1220)
ok("总时长 = 所有视频时长之和", data["stats"]["duration_sec"] == 1863 + 1220 + 1680)
ok("单课时长只算非课程包",
   B.build([{"collections": [{"season_id": 1, "title": "t", "videos": [
       {"bvid": "BVa", "title": "短", "duration": 1863},
       {"bvid": "BVb", "title": "课程包", "duration": 374584}]}]}])["stats"]["lesson_duration_sec"] == 1863)

print("\n[7] 多来源合并（一数 + 黄夫人）")
RAW_PHY = {
    "source": {"platform": "bilibili", "author": "HuangFuRen", "mid": 23630128,
               "space": "https://space.bilibili.com/23630128"},
    "collections": [
        {"season_id": 2182, "title": "选修", "total": 2, "fetched": 2, "truncated": False,
         "videos": [
             {"bvid": "BVp1", "title": "【选修3-2电磁感应】【自感现象2】30.自感练习讲解",
              "duration": 600, "pubdate": 1700000000, "views": 1000},
             {"bvid": "BVp2", "title": "【物理试卷】2025广东卷选择题",
              "duration": 900, "pubdate": 1700000001, "views": 2000},
         ]},
    ],
}
merged = B.build([RAW_MATH, RAW_PHY], now=1700500000.0)
ok("两个来源都在", [s["name"] for s in merged["sources"]] == ["一数", "黄夫人"])
ok("来源学科正确", [s["subject"] for s in merged["sources"]] == ["数学", "物理"])
ok("stats.sources = 2", merged["stats"]["sources"] == 2)
ok("视频总数 = 两边之和", merged["stats"]["total"] == 5, str(merged["stats"]["total"]))
ok("数学视频用数学规则、物理视频用物理规则",
   [v["category"] for v in merged["videos"] if v["bvid"] == "BV1"] == ["函数与导数"]
   and [v["category"] for v in merged["videos"] if v["bvid"] == "BVp1"] == ["电磁感应"],
   str([(v["bvid"], v["category"]) for v in merged["videos"]]))
ok("物理的试卷讲解单独成类（没被数学规则影响）",
   any(c["id"] == "physics:shijuan" and c["name"] == "试卷讲解" for c in merged["categories"]))
ok("分类 id 带学科前缀，不会跨学科撞车",
   len({c["id"] for c in merged["categories"]}) == len(merged["categories"])
   and all(":" in c["id"] for c in merged["categories"]))
ok("课程 id 带来源前缀，不会跨来源撞车",
   len({c["id"] for c in merged["courses"]}) == len(merged["courses"])
   and all(":" in c["id"] for c in merged["courses"]))
ok("每个来源的统计自洽",
   merged["sources"][0]["count"] == 3 and merged["sources"][1]["count"] == 2
   and sum(s["count"] for s in merged["sources"]) == merged["stats"]["total"])
ok("每个来源的课程数正确",
   merged["sources"][0]["courses"] == 2 and merged["sources"][1]["courses"] == 1)
ok("每条视频都能找到所属来源",
   all(any(s["id"] == v["source_id"] for s in merged["sources"]) for v in merged["videos"]))
ok("每条视频都能找到所属分类",
   all(any(c["id"] == v["category_id"] for c in merged["categories"]) for v in merged["videos"]))
ok("id 全局唯一（bvid）", len({v["id"] for v in merged["videos"]}) == merged["stats"]["total"])
ok("未知 mid 生成唯一来源 id（不会和别的未知来源撞车），学科暂定其他",
   B.build([{"source": {"mid": 999, "author": "某老师"}, "collections": []}])["sources"][0]["id"] == "up999"
   and B.build([{"source": {"mid": 999}, "collections": []}])["sources"][0]["subject_id"] == "other")
ok("未知 mid 用 raw 里的作者名",
   B.build([{"source": {"mid": 999, "author": "某老师"}, "collections": []}])["sources"][0]["name"] == "某老师")
ok("完全不认识的输入才退化为 other 来源",
   B.build([{"collections": []}])["sources"][0]["id"] == "other")
ok("可以手动覆盖来源名（override 用不上时应保持表里的值）",
   B.resolve_source({"source": {"mid": 14229967}})["name"] == "一数")

print("\n[7b] 学科：内置 9 科 + 自定义学科")
ok("内置学科就是语数英物化生历政地 + 其他",
   [s[1] for s in B.SUBJECTS] == ["语文", "数学", "英语", "物理", "化学", "生物", "历史", "政治",
                                  "地理", "其他"],
   str([s[1] for s in B.SUBJECTS]))
ok("输出里带 subjects 数组，且内置学科一个不少",
   len(data["subjects"]) == len(B.SUBJECTS)
   and all(s["builtin"] for s in data["subjects"]))
ok("学科行字段齐全",
   all(all(k in s for k in ("id", "name", "builtin", "count", "sources", "courses",
                            "duration_sec", "duration_text")) for s in data["subjects"]))
ok("学科顺序与内置表一致",
   [s["id"] for s in data["subjects"]] == [sid for sid, _ in B.SUBJECTS])
ok("0 视频的学科也保留（界面要用来分组）",
   any(s["count"] == 0 for s in data["subjects"]))
ok("数学学科的统计正确",
   [s for s in data["subjects"] if s["id"] == "math"][0]["count"] == 3
   and [s for s in data["subjects"] if s["id"] == "math"][0]["sources"] == 1)
ok("stats.subjects 只数有视频的学科",
   data["stats"]["subjects"] == len([s for s in data["subjects"] if s["count"]]),
   str(data["stats"]["subjects"]))
ok("学科计数之和 = 视频总数",
   sum(s["count"] for s in data["subjects"]) == data["stats"]["total"])

with_custom = B.build([RAW_MATH], now=1.0,
                      extra_subjects=[{"id": "custom-ab12", "name": "信息技术"}])
row = [s for s in with_custom["subjects"] if s["id"] == "custom-ab12"]
ok("自定义学科出现在 subjects 里且标为非内置", bool(row) and row[0]["builtin"] is False)
ok("自定义学科可以 0 视频（先建学科再导 UP 主）", row[0]["count"] == 0)
ok("重复 id 的自定义学科不会重复添加",
   len([s for s in B.build([RAW_MATH], extra_subjects=[
       {"id": "custom-ab12", "name": "信息技术"}, {"id": "custom-ab12", "name": "另一个名字"}]
   )["subjects"] if s["id"] == "custom-ab12"]) == 1)
ok("自定义学科 id 与内置冲突时不会覆盖内置",
   [s for s in B.build([RAW_MATH], extra_subjects=[{"id": "math", "name": "假数学"}])
    ["subjects"] if s["id"] == "math"][0]["builtin"] is True)
ok("脏的 extra_subjects 不崩",
   B.build([RAW_MATH], extra_subjects=[None, 3, {"name": "没有 id"}, "字符串"])["stats"]["subjects"] >= 0)

print("\n[7c] 学科自动识别")
detect_cases = [
    (["【作文】议论文审题立意三步走"], "chinese"),
    (["高中英语语法：非谓语动词"], "english"),
    (["化学平衡移动的原理"], "chemistry"),
    (["细胞呼吸与光合作用对比"], "biology"),
    (["近代中国思想解放潮流"], "history"),
    (["生活与哲学：矛盾分析法"], "politics"),
    (["洋流对气候的影响"], "geography"),
    (["【导数】切线问题"], "math"),
    (["安培力方向判断"], "physics"),
]
bad_d = [(t[0][:20], want, B.detect_subject(t)[0]) for t, want in detect_cases
         if B.detect_subject(t)[0] != want]
ok("9 个学科各给一条典型标题都能认出来（%d 例）" % len(detect_cases), not bad_d, str(bad_d[:3]))
ok("整批真实数学标题 → 数学",
   B.detect_subject(["【导数】切线", "【数列】求通项", "函数单调性证明", "圆锥曲线离心率"])[0] == "math")
ok("整批真实物理标题 → 物理",
   B.detect_subject(["安培力方向判断", "理想气体状态方程", "平抛运动", "电磁感应定律"])[0] == "physics")
ok("完全认不出来的标题 → 其他",
   B.detect_subject(["我的日常 vlog 第 3 期", "周末去哪里玩", "开箱一个键盘"])[0] == "other")
ok("空标题列表 → 其他", B.detect_subject([])[0] == "other")
ok("detect_subject 返回分数字典（含 9 科）",
   len(B.detect_subject(["数学题"])[1]) == 9, str(sorted(B.detect_subject(["数学题"])[1])))
ok("学科名本身就是最强信号（英语 > 语文 之类的歧义靠它）",
   B.detect_subject(["高考英语完形填空技巧"])[0] == "english")

print("\n[8] 脏数据与稳定性")
weird = {
    "collections": [
        {"season_id": 1, "title": None, "videos": [
            {"bvid": "BV9", "title": "【函数】单调性", "duration": "1800", "views": None, "pubdate": None},
            {"bvid": "", "title": "没有 bvid 应被跳过"},
            None,
        ]},
        None,
        {"season_id": 2, "videos": []},
    ],
}
d2 = B.build([weird], now=1.0)
ok("脏数据不崩且只留下有效视频", d2["stats"]["total"] == 1, json.dumps(d2["stats"], ensure_ascii=False))
ok("缺失标题的合集用占位名", d2["courses"][0]["title"] == "未命名合集")
ok("时长字符串被转成整数", d2["videos"][0]["duration"] == 1800)
ok("缺失播放量当 0", d2["videos"][0]["views"] == 0)
ok("缺失发布时间 → 空日期文本", d2["videos"][0]["date_text"] == "")
ok("空输入也能产出合法结构",
   B.build([{}], now=1.0)["stats"]["total"] == 0 and B.build([{}], now=1.0)["categories"] == [])
ok("空列表 / 非 dict 输入不崩", B.build([])["stats"]["total"] == 0 and B.build([None, 3])["stats"]["total"] == 0)
ok("同样输入 + 同样 now → 完全一致",
   json.dumps(B.build([RAW_MATH, RAW_PHY], now=1700500000.0), ensure_ascii=False)
   == json.dumps(B.build([RAW_MATH, RAW_PHY], now=1700500000.0), ensure_ascii=False))

print("\n[9] 输出文件与 CLI")
js = B.to_js(merged)
ok("js 里能解析回同样内容",
   json.loads(js.split("window.RESOURCES = ", 1)[1].rstrip().rstrip(";"))["stats"]["total"] == 5)
ok("js 声明了自动生成、不要手改", "自动生成" in js)
with tempfile.TemporaryDirectory() as td:
    pj, pjs = B.write_outputs(merged, Path(td))
    ok("两个文件都写出来了", pj.is_file() and pjs.is_file())
    ok("resources.json 可解析", json.loads(pj.read_text(encoding="utf-8"))["stats"]["total"] == 5)
    ok("resources.js 可解析", "window.RESOURCES" in pjs.read_text(encoding="utf-8"))
    ok("CLI 找不到原始数据时返回 2", B.main(["--raw", str(Path(td) / "nope.json")]) == 2)
    rawp = Path(td) / "raw.json"
    rawp.write_text(json.dumps(RAW_MATH, ensure_ascii=False), encoding="utf-8")
    ok("CLI 正常返回 0（单个文件）", B.main(["--raw", str(rawp), "--out-dir", td, "--top", "3"]) == 0)
    rawp2 = Path(td) / "raw2.json"
    rawp2.write_text(json.dumps(RAW_PHY, ensure_ascii=False), encoding="utf-8")
    ok("CLI 支持多个 --raw", B.main(["--raw", str(rawp), str(rawp2), "--out-dir", td]) == 0)
    data_cli = json.loads((Path(td) / "resources.json").read_text(encoding="utf-8"))
    ok("CLI 合并后确实是 2 个来源", len(data_cli["sources"]) == 2)

print("\n[10] 真实数据自洽性（爬过才有）")
real_math = HERE / "data" / "raw_bilibili.json"
real_phy = HERE / "data" / "raw_huangfuren.json"
present = [p for p in (real_math, real_phy) if p.is_file()]
if not present:
    print("  - 还没爬过原始数据，跳过")
else:
    raws = [json.loads(p.read_text(encoding="utf-8")) for p in present]
    rd = B.build(raws, now=1700500000.0)
    st = rd["stats"]
    ok("真实数据 schema 正确", rd["schema"] == "resource-lab/resources/v3")
    ok("真实数据带 10 个内置学科 + 0 个自定义",
       len(rd["subjects"]) == 10 and all(s["builtin"] for s in rd["subjects"]))
    ok("真实数据数学/物理学科计数正确",
       [s for s in rd["subjects"] if s["id"] == "math"][0]["count"] == 320
       and [s for s in rd["subjects"] if s["id"] == "physics"][0]["count"] == 533
       if real_math.is_file() and real_phy.is_file() else True,
       str([(s["name"], s["count"]) for s in rd["subjects"] if s["count"]]))
    ok("真实数据学科计数之和 = 视频总数",
       sum(s["count"] for s in rd["subjects"]) == st["total"])
    ok("真实数据视频数 = 各来源之和",
       sum(s["count"] for s in rd["sources"]) == st["total"], str(st["total"]))
    ok("真实数据 bvid 全局唯一", len({v["bvid"] for v in rd["videos"]}) == st["total"])
    ok("真实数据 url 与 bvid 一致", all(v["url"].endswith(v["bvid"]) for v in rd["videos"]))
    ok("真实数据没有空标题", all(v["title"].strip() for v in rd["videos"]))
    ok("真实数据分类计数自洽", sum(c["count"] for c in rd["categories"]) == st["total"])
    ok("真实数据每条都有来源与学科", all(v["source_id"] and v["subject_id"] for v in rd["videos"]))
    ok("真实数据里每个来源的学科唯一",
       len({s["subject_id"] for s in rd["sources"]}) == len(rd["sources"]))
    other = sum(c["count"] for c in rd["categories"] if c["name"] == "其他")
    ok("「其他」占比不超过 10%（分类规则是否够用）", other / max(1, st["total"]) <= 0.10,
       "其他 %d / %d" % (other, st["total"]))
    for s in rd["sources"]:
        mine = [v for v in rd["videos"] if v["source_id"] == s["id"]]
        ok("来源 %s 的分类都来自自己的学科" % s["name"],
           all(v["subject_id"] == s["subject_id"] for v in mine))
    if real_math.is_file() and real_phy.is_file():
        ok("数学来源确实是被当成数学分类的（函数与导数存在）",
           any(c["id"] == "math:hanshu" for c in rd["categories"]))
        ok("物理来源确实是被当成物理分类的（电磁感应存在）",
           any(c["id"] == "physics:dianci" for c in rd["categories"]))

print("\n[11] 网站页面结构")
html = (HERE / "index.html").read_text(encoding="utf-8")
ok("页面存在且非空", len(html) > 5000)
ok("用 <script src> 引入数据（file:// 下 fetch 会被 CORS 拦）",
   '<script src="data/resources.js">' in html)
ok("有 window.RESOURCES 兜底判断", "window.RESOURCES" in html)
ok("三档主题开关（自动/浅/深）",
   'data-theme="auto"' in html and 'data-theme="light"' in html and 'data-theme="dark"' in html)
ok("主题选择会记在本机", "localStorage" in html and "resource-lab.theme" in html)
ok("深色变量由属性切换（不是靠 media 硬编码）",
   '[data-theme="dark"]' in html and "prefers-color-scheme" in html)
ok("主题按钮是 5px 圆角矩形", "border-radius:5px" in html.replace(" ", ""))
ok("封面不做灰度处理（保留原色）", "grayscale" not in html)
ok("有 UP 主 / 来源筛选", "data-src" in html and "srcChips" in html and "sources()" in html)
ok("界面读取数据里的学科清单（不是写死的）",
   "DATA.subjects" in html and "function subjects()" in html and "subjectName" in html)
ok("有学科维度的筛选（学科 / UP 主 / 分类三层）",
   "S.subject" in html and "pickSubject" in html and "visibleSources" in html)
ok("侧栏按学科分组显示 UP 主", "subj-row" in html and "src-row" in html and "displaySubjects" in html)
ok("支持添加自定义学科", "data-addsubj" in html and "data-addsubjok" in html and "addSubject" in html)
# 注意：不能用第一次出现 "displaySubjects().forEach" 的位置做比较 —— render() 里的手机标签条
# 也调用它（更靠前）。这里锚定导航里那次：学科循环紧跟在「全部来源」之后。
_nav = html.index('<h4>学科 / UP 主</h4>')
_nav_end = html.index('var vis = visibleCategories();', _nav)
_nav_block = html[_nav:_nav_end]
ok("「+ 添加学科」拼在导航的学科列表里（不在别处）",
   'data-addsubj="1"' in _nav_block and 'id="newSubjName"' in _nav_block)
ok("「+ 添加学科」在「全部来源」之后、学科分组之前（否则会看起来属于最后一个学科）",
   _nav_block.index('全部来源') < _nav_block.index('data-addsubj="1"')
   < _nav_block.index('displaySubjects().forEach'),
   "全部来源@%d, addlink@%d, 学科循环@%d" % (_nav_block.index('全部来源'),
                                              _nav_block.index('data-addsubj="1"'),
                                              _nav_block.index('displaySubjects().forEach')))
ok("「+ 添加学科」在导航块里只出现一次（没有重复渲染）",
   _nav_block.count('data-addsubj="1"') == 1 and _nav_block.count('id="newSubjName"') == 1,
   "addlink x%d, input x%d" % (_nav_block.count('data-addsubj="1"'),
                               _nav_block.count('id="newSubjName"')))
ok("输入框（新建学科时）也在学科循环之前",
   _nav_block.index('id="newSubjName"') < _nav_block.index('displaySubjects().forEach'))
ok("支持删除自定义学科", "data-delsubj" in html and "deleteSubject" in html)
ok("支持删除已导入的 UP 主（带行内二次确认）",
   'data-del="' in html and "data-delok" in html and "data-delno" in html and "deleteSource" in html)
ok("支持改已有 UP 主的学科", "data-change" in html and "data-changesel" in html and "changeSourceSubject" in html)
ok("学科下拉框是动态生成的（含自定义学科）", "fillSubjectSelect" in html)
ok("深色模式下拉栏显式指定黑色背景", '[data-theme="dark"] select' in html and "background:#000" in html)
ok("用 color-scheme 让原生控件跟随主题", "color-scheme:dark" in html and "color-scheme:light" in html)
ok("卡片上标了作者", '"author"' in html and "@" in html and "class=\"author\"" in html)
ok("分类按学科隔离显示（含学科分组标题）", "visibleCategories" in html and "subj" in html)
ok("用了课程包角标（is_bundle）", "is_bundle" in html and "课程包" in html)
ok("用了 duration_label 做角标", "duration_label" in html)
ok("封面失败会自动隐藏", "referrerpolicy" in html and "visibility = 'hidden'" in html)
ok("看过/收藏存 localStorage", "resource-lab.seen" in html and "resource-lab.starred" in html)
ok("有搜索/排序/视图/筛选控件",
   all(k in html for k in ('id="q"', 'id="sort"', 'id="btnView"', 'id="btnUnseen"', 'id="btnStar"')))
ok("没有任何外部 JS/CSS 依赖（离线可看）",
   not any(x in html for x in ("cdn.", "unpkg", "jsdelivr", "googleapis")))
ok("数据缺失时给出友好提示而不是白屏", "没有找到数据文件" in html)
ok("所有用户文本都做了 HTML 转义", "function esc(" in html and "replace(/[&<>\"']/g" in html)

print("\n[12] GitHub Pages 入口页（根目录 index.html）")
# 发布页由根目录的 make_pages.py 从 resource-lab/index.html 生成。
# 这里防的是"改了源文件却忘了重新生成"——两份一旦漂移，线上页面就会和本地不一致。
root_html = HERE.parent / "index.html"
nojekyll = HERE.parent / ".nojekyll"
ok("根目录 index.html 存在（Pages 首页）", root_html.is_file())
ok(".nojekyll 存在（否则下划线文件被 Jekyll 忽略、README 被当首页）", nojekyll.is_file())
if root_html.is_file():
    rh = root_html.read_text(encoding="utf-8")
    ok("根版声明了自动生成、不要手改", rh.startswith("<!--") and "make_pages.py" in rh[:200])
    ok("根版的数据引用改成了子目录路径",
       '<script src="resource-lab/data/resources.js">' in rh)
    ok("根版不再引用同级的 data/（否则线上 404）",
       '<script src="data/resources.js">' not in rh)
    # 用生成逻辑反推，确保内容与源文件一致
    want = ("<!-- 自动生成，请勿手改：源文件是 resource-lab/index.html，"
            "由 make_pages.py 改写相对路径后生成 -->\n"
            + html.replace('<script src="data/resources.js">',
                           '<script src="resource-lab/data/resources.js">'))
    ok("根版与源文件同步（改了源文件要重新跑 make_pages.py）", rh == want,
       "" if rh == want else "内容已漂移，请运行 python make_pages.py")

print("\n===== 结果: %d 通过 / %d 失败 =====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
