# AI HOT 与 AI News Radar 信息质量对比

日期：2026-07-10

## 结论

AI News Radar 的主要问题不是“源不够”，而是默认层太宽、编辑字段被丢失、原文入口不明显。

- AI HOT 最近 24 小时默认展示 23 条精选；AI News Radar 修复前默认展示 874 条。
- AI News Radar 前五个宽聚合源贡献 673/875 条 AI 入池数据，占 76.9%。这类来源适合补盲，不适合占据默认阅读层。
- AI HOT 单条记录提供中文标题、英文原题、中文摘要、分数、分类和原文 URL；AI News Radar 的 AI HOT 解析器此前只保留中文标题、摘要和 URL，丢失了 `title_en`。
- AI News Radar 的英文标题使用 Google Translate 免费接口自动翻译，每轮最多新增 80 条并写入缓存。没有术语保护时会产生 `Codex → 法典`、`Bio Bug Bounty → 生物错误赏金` 等错误。
- AI News Radar 实际一直保留原文 URL，但整张卡片可点击而没有明确按钮，普通用户很难知道点击后会去原文。

## 信息源结构

### AI HOT 最近 24 小时精选

23 条，主要来自：

- Hacker News 热门（buzzing.cc 中文翻译）：5
- IT之家 RSS：4
- Anthropic Newsroom：3
- TechCrunch AI：2
- OpenAI 官网、Google Research、Google Developers、Mistral、MarkTechPost：各 1
- 官方/高信号 X：OpenAI Developers、Sam Altman、Meta AI、OpenBMB：各 1

其首页热点按事件聚合，当前头条分别有 17、10、14、6 个信源确认。

### AI News Radar 修复前 AI 池

875 条，主要来自：

- Buzzing：202
- Info Flow：162
- TechURLs：118
- TopHub：113
- Zeli：78
- Curated Media：40
- SocialData X：30
- NewsNow：30
- AIbase：29
- AI HOT：24
- 官方 AI 更新：10

问题在于宽聚合层与官方/精选层被放在同一个默认列表里。即使单条通过了 AI 关键词相关性，也不等于值得用户优先阅读。

## 本次修复

1. 默认页面改为 100 条高优先级精选；874 条完整 AI 池仍可在高级筛选中切换，原始全量池继续保留。
2. AI HOT 解析器保留其 `title`、`title_en`、`summary` 和 `url`，不再把英文原题丢掉。
3. 故事聚合数据继续向下游传递中文标题、英文原题、摘要和原文 URL。
4. 中文标题机翻增加 AI 产品术语修复，并同步修复旧快照在前端的显示。
5. Top 3、故事时间线和普通新闻卡片都增加英文原题展示或“查看原文 ↗”。
6. AI HOT 的编辑摘要优先用于普通列表和 Top 3；没有摘要时才回退到规则生成的相关性说明。

## 不照搬 AI HOT 的部分

AI HOT 本身也存在少量英文原题不准确、摘要被截断的问题，因此 AI News Radar 不应把它当唯一真相源。更稳妥的分工是：

- 官方 RSS/网页提供事实和原文；
- AI HOT 提供中文编辑标题、摘要和精选分；
- 多源聚合提供交叉验证；
- 宽聚合源留在完整 AI / 原始全量层，用于补盲和检索。
