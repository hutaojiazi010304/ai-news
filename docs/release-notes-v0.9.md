# v0.9 发布说明

## 一句话 TL;DR

界面从"三个视图各管一段"收敛成"一层信息架构"：一套栏目 tab + 一个精选/全量开关 + 一条时间轴；同时给标题加了 LLM 增强，给同一事件加了多源展开，给数据源加了可切换开关。

## 为什么重构

v0.7、v0.8 各自加了一个板块：伯乐精选、AI信号流、热点榜、TOP3 persona 网格，彼此平行存在但入口和心智模型不统一——用户要先搞清楚"我现在在看哪个板块"，才能决定"该看哪条"。

信息架构收敛为单层模型：内容维度收进栏目 tab，密度维度收进精选/全量开关，剩下的只有一条时间轴。理解成本降下来了，板块之间不用来回切。

## 新东西

**信息架构**
- 单层分类 tab：全部/模型/产品/开发者/行业/论文/社区/自媒体，互斥单选
- 精选/全量全局开关：精选读故事合并后的高价值池，全量读广义 AI 相关的原始池，两种模式共用同一套时间轴模板
- 主列表按时间倒序 + 按日分组，不再有独立的"故事线"或"信号流"板块
- 双视图：根目录默认手机版，右上角「视角」开关切到 `/classic/` 经典桌面版，`?view=` 参数可直接指定，两套皮肤读同一份 `data/`
- 正式域名：[news.learnprompt.pro](https://news.learnprompt.pro)（GitHub Pages 自定义域名 + Cloudflare）

**热点与点评**
- 当前热点榜不设固定条数，只要满足多信源热度阈值就上榜
- 每条精选卡片带一句话"推荐理由"——由管线抓取全文后交给 LLM 真实生成（需 `DEEPSEEK_API_KEY`），没有真实理由时区块整体隐藏，不再用模板句撑场面
- 三口味 persona 的网页展示暂时归档（样式待重设计后回归，见 docs/ROADMAP.md）；数据管线与 Skill 端日报的三口味点评照常，「论文警察」更名「较真党」

**标题与翻译**
- 标题增强：标题过短或黑话过多时，抓取原文上下文（自家抓取失败回退到 r.jina.ai）交给 LLM 改写，配 `DEEPSEEK_API_KEY` 才生效，没配就保留原标题
- `TITLE_ENHANCE_MAX_PER_RUN` 控制每次运行最多改写多少条标题，默认 30
- 翻译校验：拒答文案（"抱歉，我无法……"）和退化输出会被识别并回退到原文标题，不再污染缓存
- 时区防御：中文 feed 把北京时间错标成 GMT（如 InfoQ CN）导致的"未来条目"会被自动纠正（条目级 + feed 级 -8h 推断 + story 层未来时间钳制）

**同一事件与信源**
- 同一事件被 2 家以上信源报道时，卡片上出现"多源 N"标签，点开看每家独立标题、来源和相对时间
- 聚合源条目按原始平台再细分：X / 公众号 / HN / RSS 子来源标签
- 修了故事合并逻辑里的几处误合并/漏合并
- X 搜索排序从 Latest 换成 Top，减少低质量结果占位

**数据与开发**
- 数据同源开关：页面 URL 加 `?data=<data目录地址>` 可以让前端读取另一份 `data/`，方便验证另一个分支或 PR 的生成结果，选择记在浏览器本地
- 来源面板去重：高级筛选里的来源列表不再有重复条目

## 变化与去向对照表

| 旧的 | 现在 |
|------|------|
| 三视图（伯乐精选 / AI信号流 / 热点榜切换） | 精选/全量全局开关 + 时间轴 |
| 排序按钮（按时间/按热度手动切换） | 主列表固定按时间倒序，热度只在独立的"当前热点"榜体现 |
| 来源形态 chips（自媒体/社区手动分类） | 收进"社区"/"自媒体"栏目 tab + 聚合源子来源 chip |
| 统计条 | 源状态横幅（高级筛选里的源健康/源状态详情） |

## 兼容性

- `data/*.json` 只增字段不删改，fork 用户和 Skill 使用者无感升级，不需要改任何脚本或解析逻辑
- 旧版界面快照保留在 `/legacy/`，保留至 2026 年 8 月中旬后下线
- 新增的 `DEEPSEEK_API_KEY` 相关能力（标题增强）复用已有的可选 key，没配置的 fork 不受影响，继续按原有降级路径跑

---

## English summary

v0.9 collapses three parallel views (Scout Picks / AI Signal Flow / Hot board) into one layer: category tabs × curated/all toggle × a single chronological timeline. It adds LLM title enhancement (gated by `DEEPSEEK_API_KEY`, capped by `TITLE_ENHANCE_MAX_PER_RUN`, graceful fallback to original titles without a key), same-event multi-source expansion via an "N sources" chip, aggregator sub-source classification (X/WeChat/HN/RSS), a `?data=` data-source switch for multi-branch development, several story-merge fixes, and an X search sort change from Latest to Top. `data/*.json` only gains fields — never removes or renames them — so forks and Skill users upgrade with no action needed. The pre-v0.9 UI is archived at `/legacy/` until mid-August 2026.
