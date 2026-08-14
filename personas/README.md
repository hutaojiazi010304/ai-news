# Personas 口味目录

这里存放 persona 评分器的"口味"定义。`scripts/persona_score.py` 会读取本目录下所有 `*.md` 文件（本 README 除外），用它们作为 LLM 的 system prompt 给每日简报打分和点评。

## 内置口味

| id | 名称 | 视角 |
|----|------|------|
| `pragmatic` | 实用派（默认） | 只关心对开发者/从业者今天有什么用 |
| `cynic` | 毒舌评论员 | 拆穿营销话术和炒作，讥讽但基于事实 |
| `paper-police` | 较真党 | 只认论文/代码/benchmark 实证，对"即将推出"零容忍 |

## 文件格式

每个 persona 是一个 Markdown 文件，由 YAML frontmatter 和正文 prompt 两部分组成：

```markdown
---
id: my-persona
name: 中文名
name_en: English Name
---

正文即 system prompt……
```

frontmatter 字段：

- `id`（必填）：只允许小写字母和连字符（`[a-z-]`），需与文件名一致。
- `name`（必填）：中文显示名。
- `name_en`（必填）：英文显示名。
- `default: true`（可选）：默认口味标记，全目录只允许一个（当前是 `pragmatic`）。

注意：解析器是手写的简易 frontmatter 解析（逐行 `key: value`），不要在 frontmatter 里用嵌套结构、多行值或列表。

## 正文 prompt 必须包含

1. **口味自述**：一段话讲清这个口味的立场和关注点，人格要鲜明。
2. **评分标准**：0-100 分的分段说明，写明各分段的侧重点。
3. **输出要求**：要求模型返回 `{"score": <0-100 整数>, "review": "<一句中文点评>"}`，点评不超过 40 字，说人话，不复读标题。
4. **示例点评**：至少 3 条"输入 → 输出"示例，帮模型对齐语气和分数尺度。

## 如何贡献第 4 个口味

1. 复制任意内置口味文件作为模板，起一个符合 `[a-z-]` 的文件名（如 `personas/vc-radar.md`）。
2. 填好 frontmatter（不要写 `default: true`，默认位已被 pragmatic 占用）。
3. 按上面四要素写正文，中文文案避免翻译腔。
4. 本地验证：

   ```bash
   python scripts/persona_score.py --list-personas
   ```

   确认新口味出现在列表里。
5. 跑 `python -m pytest -q tests/test_persona_score.py` 确认解析无误。

新口味会自动参与 TOP3 多口味点评（`data/top3-personas.json`）；每日逐条评分仍只用默认口味。
