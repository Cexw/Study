# Study

学习相关的自用小项目集合。目前收录一个：

## 📚 [resource-lab](resource-lab/) — 学习资源库

把散落在 B 站的学习视频按「**学科 → UP 主 → 课程合集 → 知识点**」四层整理成可搜索的收藏库。
极简黑白界面、可切换主题；可以在网页里直接输入 B 站 UP 主名称或 ID，由本地服务现抓现整理。

当前数据：**2 位 UP 主 / 12 个课程合集 / 853 个视频**（真实抓取，非示例数据）。

| 来源 | 学科 | 合集 | 视频 |
| --- | --- | --- | --- |
| [@一数](https://space.bilibili.com/14229967) | 数学 | 7 | 320 |
| [@黄夫人](https://space.bilibili.com/23630128) | 物理 | 5 | 533 |

![学习资源库](resource-lab/screenshots/site-light.png)

**在线预览**：https://cexw.github.io/Study/ （GitHub Pages，纯静态）
> 线上版只能浏览已整理好的数据；「+ 添加 UP 主」自动抓取需要本地跑 `serve.py`，
> 在 Pages 上不可用（页面会自己提示）。

**快速开始**（详细说明见 [resource-lab/README.md](resource-lab/README.md)）

```bash
cd resource-lab

# 只看已整理好的数据：直接双击 index.html 即可，不需要服务器
start index.html

# 想自己加 UP 主（现抓现整理）：起本地服务
python serve.py                 # 默认 http://127.0.0.1:8765
```

**技术要点**

- **零依赖**：前端是单个 `index.html`（内联 CSS/JS，无 CDN/框架）；后端只用 Python 标准库
- **真实爬取**：`crawl_bilibili.py` 走 B 站公开的合集接口（带 `buvid3` cookie、请求间隔、
  重试退避、失败降级、离线缓存复跑），刻意避开有新版反爬的投稿接口
- **自动分类**：`build_resources.py` 用「学科关键词规则表 + 标题方括号章节名」把视频归到知识点，
  853 条真实标题里落进「其他」的是 **0 条**
- **本地服务**：`serve.py` 提供静态站点 + 抓取/学科/来源管理接口，支持自定义学科、改学科、
  删除已导入的 UP 主（软删除到 `data/trash/`）

**测试**（全部离线，不需要网络）

```bash
python test_crawl.py     # 13 用例 / 5982 断言
python test_build.py     # 143 断言
node   test_core.mjs     # 40 断言（前端聚合口径 vs Python 口径一致性）
python test_serve.py     # 178 断言
```

**许可 / 声明**：仅供个人学习整理使用。视频版权归原作者所有，本项目只存视频标题、链接与封面地址，
不存储视频内容。
