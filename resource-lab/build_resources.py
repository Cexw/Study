#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把爬虫抓到的原始数据，整理成网站直接可用的 resources.json / resources.js。

做四件事：
  1. 归并多个 UP 主（每份 raw 一个来源，按 mid 识别学科）；
  2. 分类：优先看标题开头【方括号】里的"章节名"，再退回整标题关键词匹配（规则表按学科分开）；
  3. 汇总：每个分类 / 每个课程合集 / 每个来源的视频数、总时长，区分"单课视频"和"课程包"；
  4. 落地：写 data/resources.json 和 data/resources.js（window.RESOURCES，让 file:// 也能用）。

为什么要按"主题"再分一层：爬到的合集是 UP 主的课程组织方式（一轮复习、最后十课……），
但学习者找东西时脑子里想的是"我要看导数 / 我要看电磁感应"，所以除了保留合集，还要有一条按知识点切的轴。

为什么要区分学科：一数讲数学、黄夫人讲物理，两套关键词规则完全不同；
数据里每条视频都带 source_id / subject_id，界面上按 UP 主分开展示。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RAW_DEFAULTS = [DATA / "raw_bilibili.json", DATA / "raw_huangfuren.json"]
OUT_DIR_DEFAULT = DATA

# ==========================================================================
# 来源表：mid → 展示名 / 学科。学科决定用哪一套分类规则。
# ==========================================================================
SOURCES = {
    14229967: {"id": "yishu", "name": "一数", "subject_id": "math", "subject": "数学"},
    23630128: {"id": "huangfuren", "name": "黄夫人", "subject_id": "physics", "subject": "物理"},
    1526560679: {"id": "yihuaer", "name": "一化儿", "subject_id": "chemistry", "subject": "化学"},
}
FALLBACK_SOURCE = {"id": "other", "name": "其他", "subject_id": "other", "subject": "其他"}

# ==========================================================================
# 学科表：9 个标准学科 + 其他。顺序就是界面上显示的顺序。
# 用户还能自己加学科（存在 data/subjects.json，由 serve.py 维护），
# 自定义学科不在这个表里，靠 extra_subjects 传进来。
# ==========================================================================
SUBJECTS: list[tuple[str, str]] = [
    ("chinese", "语文"), ("math", "数学"), ("english", "英语"), ("physics", "物理"),
    ("chemistry", "化学"), ("biology", "生物"), ("history", "历史"), ("politics", "政治"),
    ("geography", "地理"), ("other", "其他"),
]
SUBJECT_LABELS = dict(SUBJECTS)

# ==========================================================================
# 分类规则：从上往下第一个命中的生效，所以"越具体"的规则要放前面。
# 每条规则：(id, 名称, [关键词...])
# ==========================================================================
MATH_CATEGORIES: list[tuple[str, str, list[str]]] = [
    ("chugao", "初高衔接", ["初高衔接", "初升高", "上高中前", "高中前必", "初中数学", "初中"]),
    ("jichu", "基础与解法", ["基础与解法", "零基础", "从零开始", "入门", "必修", "涵盖所有", "扫盲"]),
    ("jihe", "集合与逻辑", ["集合", "逻辑用语", "充分条件", "必要条件", "充要条件", "充分必要",
                            "命题", "量词"]),
    # 三角函数必须排在"函数与导数"前面：否则"三角函数"里的"函数"二字会先被后者抢走
    ("sanjiao", "三角函数", ["三角函数", "三角", "正弦", "余弦", "解三角形", "弧度", "诱导公式",
                             "和差角", "辅助角", "正切"]),
    ("hanshu", "函数与导数", ["函数", "导数", "单调性", "奇偶性", "周期性", "指数", "对数",
                              "幂函数", "零点", "极值", "切线", "恒成立", "抽象函数", "分段函数",
                              "端点效应", "极值点偏移"]),
    ("shulie", "数列", ["数列", "等差", "等比", "递推", "裂项", "错位相减", "通项"]),
    ("budengshi", "不等式", ["不等式", "均值", "柯西", "放缩", "基本不等"]),
    ("xiangliang", "向量与复数", ["向量", "复数", "数量积", "极化恒等式", "投影", "奔驰定理", "四心"]),
    # "平面"放进立体几何：标题里的"平面"几乎都是立体几何语境（"平面向量"会先被上面的向量规则拦走）
    ("liti", "立体几何", ["立体几何", "空间向量", "三视图", "外接球", "内切球", "二面角", "异面直线",
                          "棱柱", "棱锥", "表面积", "体积", "翻折", "平面", "平行证明", "辅助线"]),
    ("jiexi", "解析几何", ["圆锥曲线", "椭圆", "双曲线", "抛物线", "离心率", "焦点弦", "解析几何",
                           "直线", "圆", "参数方程", "极坐标", "定值", "定点"]),
    ("gailv", "概率统计", ["概率", "统计", "排列组合", "计数原理", "二项式", "随机变量", "分布列",
                           "期望", "方差", "独立性检验", "抽样"]),
    ("jiqiao", "解题技巧", ["技巧", "套路", "大招", "秒杀", "模板", "压轴", "构造", "换元",
                            "常考", "易错", "考点", "题型", "新定义", "数形结合", "分类讨论", "解题思想"]),
    ("gaokao", "高考复习与应试", ["一轮", "二轮", "三轮", "复习", "高考", "模考", "真题", "冲刺",
                                  "押题", "提分", "最后十课", "总复习", "联考", "月考", "期中", "期末"]),
    ("xuexi", "学习规划与心态", ["规划", "心态", "逆袭", "经验", "建议", "怎么学", "时间管理",
                                 "习惯", "焦虑", "学习方法", "要不要", "该不该", "答疑", "怎么办",
                                 "跟不上", "不会做", "效率", "自律"]),
    ("ziliao", "资料与公告", ["资料", "教辅", "讲义", "笔记", "试卷", "答案", "预告", "公告",
                              "直播", "开课", "合集", "抽奖", "福利", "必刷", "一本通", "选择性必修"]),
]

PHYSICS_CATEGORIES: list[tuple[str, str, list[str]]] = [
    # 曲线运动要排在运动学前面：否则"圆周运动的线速度"会被运动学的"速度"抢走
    ("quxian", "曲线运动与万有引力", ["平抛", "圆周运动", "圆周", "万有引力", "天体", "卫星",
                                      "向心", "曲线运动", "抛体", "双星", "开普勒", "变轨"]),
    ("yundong", "运动学", ["运动的描述", "匀变速", "质点", "参考系", "时间时刻", "位移", "路程",
                           "矢量标量", "速度", "速率", "加速度", "打点计时器", "纸带", "竖直上抛",
                           "刹车", "自由落体", "追及", "相遇", "图像分析", "超声波测速"]),
    ("li", "力与相互作用", ["力学", "摩擦力", "弹力", "受力分析", "力的分解", "力的合成", "动态平衡",
                            "斜面", "夹砖", "活杆", "死杆", "共点力", "绳", "弹簧", "三角形法则",
                            "光滑"]),
    ("niudun", "牛顿运动定律", ["牛顿", "牛二", "牛一", "惯性", "超重", "失重", "传送带",
                                "连接体", "整体法", "隔离法"]),
    ("dongliang", "动量与碰撞", ["动量", "冲量", "碰撞", "反冲", "爆炸"]),
    ("jingdianchang", "静电场", ["电场", "库仑", "电势", "电容", "带电粒子", "静电", "场强", "等势面"]),
    ("dianlu", "恒定电流与电路", ["电路", "恒定电流", "欧姆", "电阻", "电动势", "电表", "伏安",
                                  "串并联", "闭合电路", "改装", "电学实验"]),
    # 电磁感应必须排在磁场前面："电磁感应"里包含"磁感应"，否则会被磁场规则抢走
    ("dianci", "电磁感应", ["电磁感应", "感生", "感应电流", "感应电动势", "楞次", "法拉第",
                            "自感", "涡流", "导轨"]),
    ("cichang", "磁场", ["磁场", "安培力", "洛伦兹力", "磁通量", "磁感应", "回旋"]),
    ("jiaoliu", "交变电流", ["交流电", "变压器", "远距离输电", "有效值", "中性面", "电感",
                             "输电", "四值"]),
    ("gongneng", "功与能", ["功与能", "做功", "功率", "动能", "机械能", "势能", "汽车启动",
                            "恒定功率", "钉子"]),
    ("zhenbo", "机械振动与机械波", ["简谐", "机械波", "单摆", "波动", "波速", "干涉", "衍射",
                                    "共振", "受迫", "波形", "波长"]),
    ("re", "热学", ["理想气体", "分子", "热力学", "内能", "气体压强", "选修3-3", "布朗", "晶体",
                    "温度计", "饱和汽"]),
    ("jindai", "近代物理", ["半衰期", "结合能", "光电效应", "原子", "核反应", "核能", "相对论",
                            "选修3-5", "光子", "能级", "放射性"]),
    # "光"放在最后才安全：光子/光电效应在近代物理里更靠前，光滑会先被"力"接走
    ("guang", "光学", ["光学", "折射", "全反射", "偏振", "色散", "透镜", "光路", "光"]),
    ("shiyan", "实验与仪器", ["实验", "游标卡尺", "螺旋测微器", "多用表", "示波器", "传感器", "探究"]),
    ("shijuan", "试卷讲解", ["试卷", "卷选择题", "多选题", "非选题", "压轴题", "真题", "联考", "调研卷"]),
    ("buchong", "补充专题", ["补充"]),
    ("jiqiao", "解题技巧", ["技巧", "模型", "大招", "秒杀", "总结", "套路", "易错", "考点"]),
    ("fuxi", "复习与应试", ["一轮", "二轮", "三轮", "复习", "高考", "冲刺", "模考"]),
]

SUBJECT_RULES: dict[str, list[tuple[str, str, list[str]]]] = {
    "math": MATH_CATEGORIES,
    "physics": PHYSICS_CATEGORIES,
    # 下面 7 个学科的规则比数学/物理粗：够用来"认出这个 UP 主属于哪科"和做基本归类。
    # 想更细，往对应列表里加关键词即可（规则是可读可改的，这是刻意的取舍）。
    "chinese": [
        ("zw", "作文与写作", ["作文", "写作", "素材", "议论文", "记叙文", "文采", "审题立意"]),
        ("gsw", "古诗文", ["古诗", "文言文", "诗词", "背诵", "默写", "古文", "诗歌鉴赏", "实词"]),
        ("ydlj", "现代文阅读", ["阅读", "现代文", "散文", "小说", "实用类", "论述类", "文本"]),
        ("yyjc", "语言文字运用", ["字音", "字形", "成语", "病句", "语病", "标点", "仿写", "得体"]),
        ("fxyy", "复习与应试", ["一轮", "二轮", "复习", "高考", "模考", "真题", "冲刺", "答题"]),
    ],
    "english": [
        ("ch", "词汇与语法", ["词汇", "单词", "语法", "时态", "从句", "非谓语", "虚拟语气", "短语"]),
        ("yd", "阅读理解", ["阅读", "reading", "七选五"]),
        ("wx", "完形填空", ["完形", "cloze"]),
        ("tl", "听力", ["听力", "listening"]),
        ("xz", "写作", ["书面表达", "writing", "续写", "作文题", "应用文"]),
        ("fxyy", "复习与应试", ["一轮", "二轮", "复习", "高考", "模考", "真题", "冲刺"]),
    ],
    "chemistry": [
        ("yhx", "有机化学", ["有机", "烃", "醇", "醛", "羧酸", "酯", "同分异构", "高分子",
                             "烷", "烯", "苯", "糖类", "油脂", "蛋白质", "营养物质"]),
        ("hxfy", "化学反应原理", ["化学平衡", "电离", "水解", "电化学", "速率", "热化学",
                                  "原电池", "电解", "守恒", "热效应", "焓变", "平衡移动",
                                  "弱电解质", "沉淀溶解", "反应的方向", "自发", "反应历程",
                                  "活化能", "催化剂", "基元反应"]),
        # 必修一的核心概念（离子反应/氧化还原/物质的量）原先没规则，导致大量标题掉进"其他"
        ("lzfy", "离子反应与氧化还原", ["离子反应", "离子方程式", "离子共存", "电解质",
                                        "氧化还原", "氧化剂", "还原剂", "化合价", "电子转移",
                                        "双线桥", "配平"]),
        ("jl", "物质的量与计量", ["物质的量", "摩尔", "阿伏加德罗", "气体摩尔体积", "浓度",
                                  "配制溶液", "化学计量"]),
        ("yshh", "元素化合物", ["元素", "钠", "铁", "铝", "氯", "硫", "氮", "金属", "非金属",
                                "氧化物", "方程式", "周期表", "周期律", "卤素", "硅", "铜"]),
        # 实验要排在"结构与计算"前面，否则"实验装置的结构"会被后者抢走
        ("hxsy", "化学实验", ["实验", "装置", "气体制备", "滴定", "检验", "分液", "蒸馏",
                              "萃取", "误差分析", "安全"]),
        ("wljg", "结构与计算", ["结构", "晶体", "化学键", "杂化", "电子排布", "电负性",
                                "电离能", "分子空间", "VSEPR", "晶胞", "计算"]),
        # 复习类必须放在最后：标题常同时含"高考冲刺"和明确知识点（如"高考冲刺·电化学"），
        # 知识点更该赢。只有通篇没有知识点信号的才落到这里。
        ("fxyy", "复习与应试", ["一轮", "二轮", "复习", "高考", "模考", "真题", "冲刺",
                                "题刷刷", "必刷", "刷题", "收心", "阶段", "期中", "期末",
                                "联考", "模拟", "总复习", "考前"]),
    ],
    "biology": [
        ("fzyxb", "分子与细胞", ["细胞", "蛋白", "核酸", "酶", "ATP", "光合", "呼吸",
                                 "分裂", "细胞膜"]),
        ("ycyjh", "遗传与进化", ["遗传", "基因", "DNA", "染色体", "变异", "进化", "遗传定律"]),
        ("wtyhj", "稳态与环境", ["稳态", "神经", "体液", "免疫", "种群", "群落", "生态系统"]),
        ("swsy", "生物实验", ["实验", "显微", "探究", "调查"]),
        ("fxyy", "复习与应试", ["一轮", "二轮", "复习", "高考", "模考", "真题", "冲刺"]),
    ],
    "history": [
        ("zggd", "中国古代史", ["先秦", "秦汉", "唐宋", "明清", "古代", "王朝", "制度"]),
        ("zgjd", "中国近现代史", ["近代", "现代", "鸦片", "辛亥", "抗战", "新中国", "改革开放"]),
        ("sjs", "世界史", ["世界", "西方", "欧美", "文艺复兴", "工业革命", "二战", "希腊", "罗马"]),
        ("sldt", "史料与答题", ["史料", "材料题", "答题", "小论文", "时间线", "思维导图"]),
        ("fxyy", "复习与应试", ["一轮", "二轮", "复习", "高考", "模考", "真题", "冲刺"]),
    ],
    "politics": [
        ("jjsh", "经济生活", ["经济", "市场", "价格", "消费", "企业", "财政", "税收", "货币"]),
        ("zzsh", "政治生活", ["政治", "公民", "政府", "人大", "政党", "国际", "外交", "民主"]),
        ("whsh", "文化生活", ["文化", "文化自信", "传统", "民族精神", "文化交流"]),
        ("shyzx", "生活与哲学", ["哲学", "唯物", "辩证", "矛盾", "认识论", "价值观", "方法论"]),
        ("szdt", "时政与答题", ["时政", "热点", "答题", "主观题", "模板"]),
        ("fxyy", "复习与应试", ["一轮", "二轮", "复习", "高考", "模考", "真题", "冲刺"]),
    ],
    "geography": [
        ("zrdl", "自然地理", ["大气", "气候", "水文", "洋流", "地貌", "岩石", "地球运动",
                               "时区", "自然带"]),
        ("rwdl", "人文地理", ["人口", "城市", "农业", "工业", "交通", "区位", "产业"]),
        ("qydl", "区域地理", ["区域", "中国地理", "世界地理", "区域发展", "生态", "环境问题"]),
        ("dtdzx", "地图与等值线", ["地图", "等值线", "等高线", "经纬", "读图"]),
        ("fxyy", "复习与应试", ["一轮", "二轮", "复习", "高考", "模考", "真题", "冲刺"]),
    ],
    "other": [("qita", "其他", [])],
}
DEFAULT_CATEGORY = ("qita", "其他")

# 这些分类是"阶段名 / 系列名"而不是"知识点"：标题开头的【一轮复习】【资料】【补充】【试卷】
# 不该压过正文里的知识点，所以第一遍（只看方括号）时跳过它们，留给第二遍（整标题）再匹配。
# 例如「【一轮复习】导数专题」应该进"函数与导数"，而不是进"高考复习与应试"。
#
# 注意：各学科给这类分类起的 id 不一样（数学 gaokao、物理 fuxi、化学/语文 fxyy…），
# 只靠 id 黑名单极易漏 —— 化学就漏过一次（"复习与应试"没被跳过，吃掉了 43% 的视频）。
# 所以额外按 **名称** 兜底匹配：新增学科时只要阶段类名字对得上就自动生效。
BRACKET_SKIP = {"buchong", "ziliao", "gaokao", "fuxi", "xuexi", "shijuan"}
BRACKET_SKIP_NAMES = ("复习与应试", "复习与考试", "资料与公告", "补充专题", "试卷讲解",
                      "学习规划与心态", "时政与答题", "史料与答题", "复习与备考")

BUNDLE_SECONDS = 3 * 3600
BRACKET_RE = re.compile(r"^\s*(?:\d+\s*[.、]\s*)?[【\[「]([^】\]」]{1,24})[】\]」]")


def _match(title: str, rules: list[tuple[str, str, list[str]]], skip: set[str] | None = None):
    for cid, name, kws in rules:
        if skip is not None and (cid in skip or name in BRACKET_SKIP_NAMES):
            continue
        hit = [k for k in kws if k in title]
        if hit:
            return cid, name, hit
    return None


def classify(title: str, subject: str = "math") -> tuple[str, str, list[str]]:
    """归类：(分类 id, 分类名, 命中关键词)。

    先用标题开头的【方括号】当章节信号（UP 主的标题里这里最可信），再退回整标题匹配。
    """
    t = str(title or "")
    rules = SUBJECT_RULES.get(subject) or SUBJECT_RULES["other"]
    m = BRACKET_RE.match(t)
    if m:
        got = _match(m.group(1), rules, skip=BRACKET_SKIP)
        if got:
            return got
    got = _match(t, rules)
    if got:
        return got
    return DEFAULT_CATEGORY[0], DEFAULT_CATEGORY[1], []


def extract_tags(title: str, matched: list[str], limit: int = 4) -> list[str]:
    """标签 = 【方括号里的系列名】 + 命中的关键词（去重、保序）。"""
    t = str(title or "")
    tags: list[str] = []
    i = 0
    while True:
        starts = [p for p in (t.find("【", i), t.find("[", i), t.find("「", i)) if p >= 0]
        if not starts:
            break
        s = min(starts)
        close = {"【": "】", "[": "]", "「": "」"}[t[s]]
        e = t.find(close, s)
        if e < 0:
            break
        seg = t[s + 1:e].strip()
        if 0 < len(seg) <= 14 and seg not in tags:
            tags.append(seg)
        i = e + 1
    for k in matched:
        if k not in tags:
            tags.append(k)
    return tags[:limit]


def clock(sec: int) -> str:
    """秒 → 播放器风格的时长（小时可能超过 24，所以不折成天）。"""
    sec = max(0, int(sec or 0))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def human_duration(sec: int) -> str:
    """秒 → 人话时长，用于分类/课程/来源的汇总（409643 → 113 小时 47 分）。"""
    sec = max(0, int(sec or 0))
    h, m = sec // 3600, (sec % 3600) // 60
    if h:
        return f"{h} 小时 {m} 分" if m else f"{h} 小时"
    if m:
        return f"{m} 分钟"
    return f"{sec} 秒"


def duration_label(sec: int) -> str:
    """卡片角标用的时长文案：课程包说"N 小时"，普通视频说"MM:SS / HH:MM:SS"。"""
    sec = max(0, int(sec or 0))
    if sec >= BUNDLE_SECONDS:
        return "%d 小时" % int(round(sec / 3600))
    return clock(sec)


def _date_text(ts: int) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d", time.localtime(int(ts)))


def _mid_of(raw) -> int | None:
    try:
        return int((raw.get("source") or {}).get("mid"))
    except (TypeError, ValueError):
        return None


# 学科自己的名字是最强信号，但标题里不一定写进规则表，所以在识别时单独加权。
SUBJECT_ALIASES: dict[str, list[str]] = {
    "chinese": ["语文", "汉语", "chinese"],
    "math": ["数学", "math"],
    "english": ["英语", "english"],
    "physics": ["物理", "physics"],
    "chemistry": ["化学", "chemistry"],
    "biology": ["生物", "biology"],
    "history": ["历史", "history"],
    "politics": ["政治", "politics"],
    "geography": ["地理", "geography"],
}


def _count_hits(title: str, rules: list[tuple[str, str, list[str]]], sid: str = "") -> int:
    """一条标题在该学科规则里命中了多少个不同关键词（方括号和整标题都算，不重复计）。

    命中学科自身的名字（"英语""物理"…）额外加权重，因为那是最直接的证据。
    """
    hit: set[str] = set()
    m = BRACKET_RE.match(title)
    if m:
        seg = m.group(1)
        for _cid, _name, kws in rules:
            for k in kws:
                if k in seg:
                    hit.add(k)
    for _cid, _name, kws in rules:
        for k in kws:
            if k in title:
                hit.add(k)
    alias_hits = sum(1 for a in SUBJECT_ALIASES.get(sid, []) if a in title)
    return len(hit) + 3 * alias_hits


def detect_subject(titles, sample: int = 150, threshold: float = 0.3) -> tuple[str, dict]:
    """拿一批标题去各学科规则表打分，猜这个 UP 主讲哪个学科。

    打分 = 命中关键词的**总个数**（不是"有没有命中"），这样"高考英语完形填空"会算到
    英语 2~3 分而不是每科都 1 分。平票时按学科常规顺序（语数英物化生历政地）取靠前的那个，
    避免结果随 id 字母序乱跳。
    阈值看的是"能识别出学科的标题比例"：最高分那科连 30% 的标题都认不出来，
    就返回 other，交给用户自己选，而不是瞎猜。
    """
    texts = [str(t) for t in (titles or [])][:sample]
    if not texts:
        return "other", {}
    order = {sid: i for i, (sid, _name) in enumerate(SUBJECTS)}
    scores: dict[str, int] = {}
    covered: dict[str, int] = {}
    for sid, rules in SUBJECT_RULES.items():
        if sid == "other":
            continue
        hits = titled = 0
        for t in texts:
            h = _count_hits(t, rules, sid)
            if h:
                titled += 1
                hits += h
        scores[sid] = hits
        covered[sid] = titled
    if not scores:
        return "other", {}
    best = max(scores, key=lambda k: (scores[k], -order.get(k, 99)))
    if covered.get(best, 0) < max(1, len(texts) * threshold):
        return "other", scores
    return best, scores


def resolve_source(raw: dict, override: dict | None = None) -> dict:
    """按 mid 查来源表；查不到就用 override 或兜底。"""
    src = raw.get("source") or {}
    mid = src.get("mid")
    try:
        mid_int = int(mid)
    except (TypeError, ValueError):
        mid_int = None
    info = SOURCES.get(mid_int) if mid_int is not None else None
    if info is None and override is None and mid_int is not None:
        # 不在表里的 UP 主（用户自己加的）：给一个唯一 id，别和别的未知来源撞车
        out = {"id": f"up{mid_int}", "name": src.get("author") or f"UP {mid_int}",
               "subject_id": "other", "subject": "其他"}
    else:
        out = dict(info or FALLBACK_SOURCE)
    if override:
        out.update({k: v for k, v in override.items() if v})
    out["mid"] = mid_int if mid_int is not None else mid
    out["space"] = src.get("space") or (f"https://space.bilibili.com/{mid_int}" if mid_int else "")
    out["author_raw"] = src.get("author") or ""
    return out


def _subject_row(sid: str, name: str, videos: list, courses: list, builtin: bool) -> dict:
    """一个学科的汇总行（含 0 视频的学科，界面要用它来分组和做下拉选择）。"""
    mine = [v for v in videos if v["subject_id"] == sid]
    sec = sum(v["duration"] for v in mine)
    return {
        "id": sid, "name": name, "builtin": builtin,
        "count": len(mine),
        "sources": len({v["source_id"] for v in mine}),
        "courses": sum(1 for c in courses if c["subject_id"] == sid),
        "duration_sec": sec, "duration_text": human_duration(sec),
    }


def build(raws: list[dict], now: float | None = None, source_overrides: dict | None = None,
          extra_subjects: list | None = None) -> dict:
    """原始数据（可多份）→ 网站数据。纯函数：同样的输入 + 同样的 now 一定得到同样的输出。

    source_overrides: {mid: {id, name, subject_id, subject}} —— 给不在 SOURCES 表里的
    UP 主（用户在界面上自己添加的）指定来源信息与学科。
    extra_subjects: [{id, name}] —— 用户自定义的学科（内置的 9 科在 SUBJECTS 里）。
    """
    now = time.time() if now is None else now
    overrides = source_overrides or {}
    videos: list[dict] = []
    courses: list[dict] = []
    sources: list[dict] = []
    order = 0

    for raw in raws:
        if not isinstance(raw, dict):
            continue
        src = resolve_source(raw, overrides.get(_mid_of(raw)))
        s_sec = 0
        s_count = 0
        for c in (raw.get("collections") or []):
            if not isinstance(c, dict):
                continue
            cid = str(c.get("season_id") or c.get("id") or "")
            ctitle = c.get("title") or "未命名合集"
            csec = 0
            for v in (c.get("videos") or []):
                if not isinstance(v, dict):
                    continue
                bvid = str(v.get("bvid") or "").strip()
                title = str(v.get("title") or "").strip()
                if not bvid or not title:
                    continue
                cat_id, cat_name, matched = classify(title, src["subject_id"])
                dur = max(0, int(v.get("duration") or 0))
                csec += dur
                s_sec += dur
                s_count += 1
                videos.append({
                    "id": bvid,                       # 全站唯一：bvid 本身唯一
                    "bvid": bvid,
                    "title": title,
                    "url": v.get("url") or f"https://www.bilibili.com/video/{bvid}",
                    "cover": v.get("cover") or "",
                    "duration": dur,
                    "duration_text": clock(dur),
                    "duration_label": duration_label(dur),
                    "is_bundle": dur >= BUNDLE_SECONDS,
                    "views": int(v.get("views") or 0),
                    "danmaku": int(v.get("danmaku") or 0),
                    "pubdate": int(v.get("pubdate") or 0),
                    "date_text": _date_text(v.get("pubdate") or 0),
                    "source_id": src["id"],
                    "author": src["name"],
                    "subject_id": src["subject_id"],
                    "subject": src["subject"],
                    "course_id": f"{src['id']}:{cid}",   # 加前缀，避免不同 UP 主的 season_id 撞车
                    "course_title": ctitle,
                    "category_id": f"{src['subject_id']}:{cat_id}",
                    "category": cat_name,
                    "tags": extract_tags(title, matched),
                    "order": order,
                })
                order += 1
            courses.append({
                "id": f"{src['id']}:{cid}",
                "season_id": cid,
                "title": ctitle,
                "intro": c.get("intro") or "",
                "cover": c.get("cover") or "",
                "source_id": src["id"],
                "author": src["name"],
                "subject_id": src["subject_id"],
                "subject": src["subject"],
                "count": sum(1 for v in videos if v["course_id"] == f"{src['id']}:{cid}"),
                "duration_sec": csec,
                "duration_text": human_duration(csec),
            })
        sources.append({
            "id": src["id"], "name": src["name"], "mid": src["mid"],
            "subject_id": src["subject_id"], "subject": src["subject"],
            "space": src["space"],
            "count": s_count,
            "courses": sum(1 for c in courses if c["source_id"] == src["id"]),
            "duration_sec": s_sec,
            "duration_text": human_duration(s_sec),
            "bundles": sum(1 for v in videos if v["source_id"] == src["id"] and v["is_bundle"]),
            "lessons": sum(1 for v in videos if v["source_id"] == src["id"] and not v["is_bundle"]),
        })

    # 分类汇总（按学科命名空间隔离），按视频数从多到少
    buckets: dict[str, dict] = {}
    for v in videos:
        b = buckets.setdefault(v["category_id"], {
            "id": v["category_id"], "name": v["category"],
            "subject_id": v["subject_id"], "subject": v["subject"],
            "count": 0, "duration_sec": 0,
        })
        b["count"] += 1
        b["duration_sec"] += v["duration"]
    categories = sorted(buckets.values(), key=lambda x: (x["subject_id"], -x["count"], x["name"]))
    for c in categories:
        c["duration_text"] = human_duration(c["duration_sec"])

    total_sec = sum(v["duration"] for v in videos)
    bundles = sum(1 for v in videos if v["is_bundle"])
    lesson_sec = sum(v["duration"] for v in videos if not v["is_bundle"])

    # 学科汇总：内置 9 科永远都在（0 个视频也留着，界面上要用来分组），自定义学科全部保留
    subjects_out = [_subject_row(sid, name, videos, courses, True) for sid, name in SUBJECTS]
    seen_subjects = {s["id"] for s in subjects_out}
    for extra in (extra_subjects or []):
        if not isinstance(extra, dict):
            continue
        sid = str(extra.get("id") or "").strip()
        if not sid or sid in seen_subjects:
            continue
        seen_subjects.add(sid)
        subjects_out.append(_subject_row(sid, extra.get("name") or sid, videos, courses, False))

    return {
        "schema": "resource-lab/resources/v3",
        "generated_at": int(now * 1000),
        "generated_at_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        "sources": sources,
        "subjects": subjects_out,
        "stats": {
            "total": len(videos),
            "courses": len(courses),
            "categories": len(categories),
            "sources": len(sources),
            "subjects": len({s["id"] for s in subjects_out if s["count"]}),
            "duration_sec": total_sec,
            "duration_text": human_duration(total_sec),
            # 单课 vs 课程包（超过 3 小时的超长视频），两者量级差太多，混在一起会误导
            "bundles": bundles,
            "lessons": len(videos) - bundles,
            "lesson_duration_sec": lesson_sec,
            "lesson_duration_text": human_duration(lesson_sec),
            "bundle_threshold_sec": BUNDLE_SECONDS,
        },
        "categories": categories,
        "courses": courses,
        "videos": videos,
    }


def build_one_source(raw: dict, subject_id: str | None = None, name: str | None = None,
                     slug: str | None = None, now: float | None = None,
                     subject_label: str | None = None, extra_subjects: list | None = None) -> dict:
    """只构建一个来源（给"用户输入 UP 名自动抓取"用）。

    返回 {"source", "courses", "categories", "videos", "subjects", "stats"}，字段与整体构建
    完全一致，前端可以直接把它并进当前数据里，不用重新加载整份 resources.js。
    """
    mid = _mid_of(raw)
    known = SOURCES.get(mid) if mid is not None else None
    if known:
        base = {"id": known["id"], "name": known["name"],
                "subject_id": known["subject_id"], "subject": known["subject"]}
    else:
        raw_name = (raw.get("source") or {}).get("author") or ""
        base = {"id": (f"up{mid}" if mid is not None else "up"),
                "name": raw_name or (f"UP {mid}" if mid is not None else "未知来源"),
                "subject_id": "other", "subject": "其他"}
    override = dict(base)
    if name:
        override["name"] = name
    if slug:
        override["id"] = slug
    if subject_id:
        override["subject_id"] = subject_id
        # 自定义学科的名字从界面传进来（内置的查表即可），否则会被写成"其他"
        override["subject"] = subject_label or SUBJECT_LABELS.get(subject_id, subject_id)
    full = build([raw], now=now, source_overrides={mid: override},
                 extra_subjects=extra_subjects)
    return {
        "source": full["sources"][0] if full["sources"] else None,
        "courses": full["courses"],
        "categories": full["categories"],
        "videos": full["videos"],
        "subjects": full["subjects"],
        "stats": full["stats"],
        "generated_at": full["generated_at"],
        "generated_at_text": full["generated_at_text"],
    }


def load_subjects_manifest(path: str | Path) -> list:
    """读 data/subjects.json 里的自定义学科；坏了就当没有，不让它挡住整个构建。"""
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("custom") if isinstance(data, dict) else data
    out = []
    for it in (items or []):
        if isinstance(it, dict) and it.get("id"):
            out.append({"id": str(it["id"]), "name": str(it.get("name") or it["id"])})
    return out


def apply_sources_to_dataset(base: dict, slices: list[dict], now: float | None = None) -> dict:
    """把若干个「单来源切片」合并进一份完整数据（服务端持久化时用）。

    完全按 build() 的口径重建，避免"增量合并"和"整体重算"出现两套结果。
    """
    raws = base.get("_raws") or []
    return build(raws, now=now)


def discover_raws(data_dir: Path | None = None) -> list[Path]:
    """默认把 data/raw_*.json 全扫进来。

    必须和 serve.py 的重建口径一致，否则"命令行重建"和"服务端重建"会得出不同数据集
    （比如 data/ 里多了一个用户加的 raw 文件，命令行却看不见它）。
    """
    d = data_dir or DATA
    files = sorted(p for p in d.glob("raw_*.json") if p.is_file())
    return files or [p for p in RAW_DEFAULTS if p.is_file()]


def to_js(data: dict) -> str:
    """包成 window.RESOURCES = {...}，让 file:// 直接双击也能用。"""
    return ("/* 自动生成，请勿手改：由 build_resources.py 从 data/raw_*.json 生成 */\n"
            "window.RESOURCES = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")


def write_outputs(data: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pj = out_dir / "resources.json"
    pjs = out_dir / "resources.js"
    pj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    pjs.write_text(to_js(data), encoding="utf-8")
    return pj, pjs


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="build_resources.py",
                                 description="原始爬取数据 → 网站数据（按学科分类 + 汇总）")
    ap.add_argument("--raw", nargs="+", default=None,
                    help="原始数据路径；不给就自动扫描 data/raw_*.json（与 serve.py 一致）")
    ap.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT), help="输出目录")
    ap.add_argument("--subjects", default=str(DATA / "subjects.json"),
                    help="自定义学科清单（由 serve.py 维护，没有就忽略）")
    ap.add_argument("--top", type=int, default=8, help="摘要里每个学科最多列出多少个分类")
    args = ap.parse_args(argv)

    raw_paths = [Path(p) for p in args.raw] if args.raw else discover_raws()
    if not raw_paths:
        print("错误：data/ 下没有 raw_*.json，先跑 python crawl_bilibili.py 抓一次", file=sys.stderr)
        return 2

    raws, missing = [], []
    for path in raw_paths:
        if not path.is_file():
            missing.append(str(path))
            continue
        try:
            raws.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            print("错误：%s 不是合法 JSON —— %s" % (path, e), file=sys.stderr)
            return 2
    if missing:
        print("错误：找不到原始数据：\n  " + "\n  ".join(missing), file=sys.stderr)
        print("      先跑 python crawl_bilibili.py（一数）/ python crawl_bilibili.py --mid 23630128 "
              "--author HuangFuRen --out data/raw_huangfuren.json（黄夫人）", file=sys.stderr)
        return 2
    if not raws:
        print("错误：没有可用的原始数据", file=sys.stderr)
        return 2

    data = build(raws, extra_subjects=load_subjects_manifest(args.subjects))
    pj, pjs = write_outputs(data, Path(args.out_dir))
    st = data["stats"]

    print("已整理 %d 个视频 / %d 个课程合集 / %d 个来源 / %d 个学科 / %d 个主题分类，总时长 %s"
          % (st["total"], st["courses"], st["sources"], st["subjects"], st["categories"],
             st["duration_text"]))
    print("其中单课视频 %d 个（%s），课程包 %d 个（单个视频超过 %d 小时）"
          % (st["lessons"], st["lesson_duration_text"], st["bundles"], BUNDLE_SECONDS // 3600))
    print("\n按学科：")
    for sub in data["subjects"]:
        if not sub["count"] and sub["builtin"]:
            continue
        tag = "" if sub["builtin"] else "（自定义）"
        print("  [%s]%s %d 个视频 / %d 个来源 / %s"
              % (sub["name"], tag, sub["count"], sub["sources"], sub["duration_text"]))
        for s in data["sources"]:
            if s["subject_id"] == sub["id"]:
                print("      └ @%s（mid=%s）：%d 个 / %d 个合集"
                      % (s["name"], s["mid"], s["count"], s["courses"]))
    print("\n主题分类分布：")
    for sub in data["subjects"]:
        rows = [c for c in data["categories"] if c["subject_id"] == sub["id"]]
        if not rows:
            continue
        print("  [%s]" % sub["name"])
        for c in rows[:args.top]:
            pct = (c["count"] / st["total"] * 100) if st["total"] else 0
            print("    %-14s %4d 个  %5.1f%%  %s" % (c["name"], c["count"], pct, c["duration_text"]))
    print("\n已写出：\n  %s（%.0f KB）\n  %s（%.0f KB）"
          % (pj, pj.stat().st_size / 1024, pjs, pjs.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
