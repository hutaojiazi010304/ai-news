# Scoring backtest report

- Window: last 14 days (83725 archived items)
- Baseline: `b7e7833~1` | Candidate: working tree
- Kept (baseline -> candidate): 10401 -> 10078 (12.4% -> 12.0%)
- Flips AI->not_ai: **327** | not_ai->AI: **4**

## Flip samples (AI -> not_ai)

- [opmlrss] Quoting Karen Kwok for Reuters Breakingviews (ai_product_update 0.71 -> not_ai)
- [buzzing] “写题词”并非一项技能——我们必须停止自欺欺人 (ai_general 0.65 -> not_ai)
- [buzzing] 贝尔法斯特暴力事件引发右翼阵营内斗 (ai_general 0.65 -> not_ai)
- [buzzing] 风投对法律初创企业可谓青睐有加。安德森·霍洛维茨（Andreessen Horowitz）刚刚投资了一家正在抢占专利律师业务的初创企业。 (ai_general 0.65 -> not_ai)
- [buzzing] 您的搜索结果正在被“Sloptimized” (ai_product_update 0.65 -> not_ai)
- [buzzing] 北京在台湾附近发现疑似日本间谍飞机 - South China Morning Post (model_release 0.65 -> not_ai)
- [techurls] Why high-flying chip stocks are suddenly looking vulnerable (infra_compute 0.65 -> not_ai)
- [buzzing] 巴勒斯坦人称，以色列定居者阻碍了约旦河西岸某基督教村庄附近的灭火工作 - Reuters (model_release 0.65 -> not_ai)
- [buzzing] 北京在台湾附近发现疑似日本间谍飞机 - South China Morning Post (model_release 0.65 -> not_ai)
- [buzzing] 随着动荡的一周持续，英国和欧洲股市料将下跌 (ai_general 0.65 -> not_ai)
- [buzzing] SpaceX首次公开募股（IPO）认购需求达2500亿美元。这对上市首日交易意味着什么 - Yahoo Finance (model_release 0.65 -> not_ai)
- [tophub] Respan Gateway (ai_product_update 0.65 -> not_ai)
- [tophub] Kimi Work (ai_product_update 0.65 -> not_ai)
- [tophub] Whistle (ai_product_update 0.65 -> not_ai)
- [buzzing] 吉姆·克莱默将高企的CPI称为“人为通胀”——这对股市意味着什么 - CNBC (model_release 0.65 -> not_ai)
- [buzzing] 如果克劳德·法布尔不再帮助你，你永远都不会知道 (model_release 0.65 -> not_ai)
- [buzzing] 研究显示，去年印度尼西亚的降雨和山体滑坡导致全球最稀有的类人猿中有7%死亡 - The Guardian (model_release 0.65 -> not_ai)
- [buzzing] 花莲的漫漫长夜：台湾原住民眼中的“中国威胁” - The Diplomat – Asia-Pacific (model_release 0.65 -> not_ai)
- [buzzing] 工业机器人初创公司Mujin正筹集资金，计划于2030年前上市 (robotics 0.65 -> not_ai)
- [buzzing] 联合国将调查黎巴嫩境内“各方”违反国际法的行为 - Haaretz (model_release 0.65 -> not_ai)
- [buzzing] Tether在14亿美元融资轮中投资德国机器人初创公司Neura (robotics 0.65 -> not_ai)
- [buzzing] Show HN: 基于Excel开发的2D和3D机械臂模拟器 (robotics 0.65 -> not_ai)
- [buzzing] 特朗普称美国将再次对伊朗发动袭击 (developer_tool 0.65 -> not_ai)
- [techurls] Apple wants Europe to blink (ai_general 0.65 -> not_ai)
- [techurls] Your Google Home speaker is about to understand you a lot better (model_release 0.65 -> not_ai)
- [techurls] Ambani-backed Addverb wants $100 million to build India’s answer to Unitree and Tesla Optimus (robotics 0.65 -> not_ai)
- [buzzing] Show HN: OpenClaw 和 Hermes Agent 通过 XMPP 相互通信 (agent_workflow 0.65 -> not_ai)
- [buzzing] Show HN: Loom，一个用于编码代理的开源交付框架 (ai_tech 0.65 -> not_ai)
- [techurls] Rotomate raises €2.1M pre-seed to put a reliability engineer inside every factory (industry_business 0.65 -> not_ai)
- [techurls] VCs can't get enough of legal startups. Andreessen Horowitz just invested in one taking work from patent lawyers. (ai_general 0.65 -> not_ai)

## Flip samples (not_ai -> AI)

- [techurls] Microsoft C.E.O. Satya Nadella Says ‘Everyone Is a Stakeholder’ in A.I. (-> ai_general 0.65)
- [techurls] SpaceX Has $30 Billion Deal to Provide Google With A.I. Computing Power (-> ai_product_update 0.65)
- [techurls] The Public Should Own Half of the Big A.I. Companies (-> ai_general 0.65)
- [newsnow] The Public Should Own Half of the Big A.I. Companies (-> ai_general 0.65)

## Label moves (kept items)

- ai_product_update -> ai_general: 42
- model_release -> ai_product_update: 22
- agent_workflow -> ai_general: 19
- model_release -> ai_general: 17
- developer_tool -> ai_general: 12
- research_paper -> ai_general: 10
- developer_tool -> ai_product_update: 8
- infra_compute -> ai_general: 7
- ai_tech -> ai_general: 6
- model_release -> developer_tool: 5
- model_release -> agent_workflow: 4
- research_paper -> ai_product_update: 4
- developer_tool -> industry_business: 3
- industry_business -> ai_general: 3
- model_release -> infra_compute: 3
- infra_compute -> ai_product_update: 3
- robotics -> ai_general: 3
- agent_workflow -> ai_tech: 2
- developer_tool -> agent_workflow: 2
- agent_workflow -> industry_business: 2

## Per-site keep counts (baseline -> candidate)

- tophub: 1269 -> 1260 / 38399  **(-9)**
- buzzing: 2501 -> 2312 / 22456  **(-189)**
- iris: 2017 -> 1995 / 11477  **(-22)**
- techurls: 1574 -> 1476 / 5731  **(-98)**
- newsnow: 444 -> 440 / 2757  **(-4)**
- zeli: 1189 -> 1189 / 1189
- aihot: 511 -> 511 / 511
- aibase: 386 -> 386 / 386
- followbuilders: 149 -> 149 / 367
- opmlrss: 233 -> 232 / 324  **(-1)**
- official_ai: 122 -> 122 / 122
- aibreakfast: 6 -> 6 / 6
