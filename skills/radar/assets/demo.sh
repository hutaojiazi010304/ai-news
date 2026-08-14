#!/bin/bash
echo "🛰  ai-radar · 零API · 零Key · 读取公开雷达数据"
curl -s https://learnprompt.github.io/ai-news-radar/data/latest-24h.json -o /tmp/radar-24h.json
python3 - <<'EOF'
import json, datetime
d = json.load(open('/tmp/radar-24h.json'))
gen = d['generated_at'][:16].replace('T', ' ')
print(f"📡 数据时间 {gen} UTC | {d['total_items']} 条AI信号 | {d['source_count']} 个信源")
print()
items = sorted(d['items_ai'], key=lambda i: (i['source_tier_rank'], -i['ai_score']))
GROUPS = [('model_release', '🚀 模型发布', 4), ('ai_product_update', '📦 产品与更新', 3), ('developer_tool', '🔧 开发者工具', 3)]
for label, title, n in GROUPS:
    hits = [i for i in items if i['ai_label'] == label][:n]
    if not hits:
        continue
    print(title)
    for i in hits:
        t = i['title'][:46] + ('…' if len(i['title']) > 46 else '')
        print(f"  · {t}  ⟵ {i['source']}")
    print()
print("…完整简报含原文链接，随便问：\"OpenAI最近发了什么\" / \"看下故事线\"")
EOF
