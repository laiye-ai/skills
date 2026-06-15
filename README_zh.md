<div align="center">

# 来也 Agent Skills

**由 [来也科技 (Laiye)](https://laiye.com) 构建并维护的开源 AI Agent 技能集合。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Skill Format: SKILL.md](https://img.shields.io/badge/format-SKILL.md-blue.svg)](#技能格式)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](./CONTRIBUTING.md)

[English](./README.md) · [中文](./README_zh.md)

</div>

---

## 项目简介

**来也 Agent Skills** 是一个精选、开源的 *Agent 技能（Skill）* 库 —— 每个技能都是自包含、可被模型按需加载的能力单元，用领域知识、操作流程和工具来扩展 AI Agent 的能力。

每个技能是一个目录，包含一个 `SKILL.md` 文件以及它所需的脚本和资源。Agent 通过技能的 `name` 与 `description` 进行发现，仅在任务匹配时**按需加载**完整指令 —— 从而在保持基础上下文精简的同时，让深度的专业能力在需要时随取随用。

本仓库汇集了来也在产品与研究中沉淀的技能，并以开放的方式打包，方便社区使用、改造与共建。

## 核心特性

- **渐进式披露（Progressive disclosure）** —— 始终加载的只有简短的名称 + 描述；技能正文仅在相关时才被拉入上下文，使大量技能可以共存而不撑爆上下文。
- **自包含** —— 每个技能在单一目录内打包自己的指令、脚本与参考文件。
- **模型无关** —— 技能本质是 Markdown + 资源文件，可在任何支持 `SKILL.md` 约定的 Agent 运行时中工作。
- **可组合** —— 技能可以调用脚本、引用其他技能，并组合起来处理多步骤工作流。
- **开放可扩展** —— 采用 MIT 协议；新增一个技能只需新增一个文件夹。

## 目录结构

```
skills/
├── README.md                    # 英文说明
├── README_zh.md                 # 本文件（中文）
├── LICENSE                      # MIT 协议
├── CONTRIBUTING.md              # 如何新增或改进技能
└── skills/                      # 技能库 —— 每个技能一个文件夹
    ├── cashflow-daily-report/   # 拉取银行流水并生成现金流日报
    │   ├── SKILL.md
    │   ├── scripts/             # 取数、汇总、渲染脚本
    │   ├── references/          # 输出格式与详细说明
    │   └── data/                # 示例流水数据
    ├── expense-reimbursement/   # 端到端报销单自动化
    │   ├── SKILL.md
    │   ├── config.json          # 技能配置
    │   ├── scripts/
    │   └── references/
    └── geo-content-scorer/      # 评估内容在 AI 搜索引擎中的可被引用度（GEO 评分）
        ├── SKILL.md
        ├── scripts/
        ├── references/
        └── assets/
```

每个技能目录必有一个 `SKILL.md`；其余内容（`scripts/`、`references/`、
`data/`、`config.json` 等）均为可选，按需加载。

## 技能格式

一个技能是一个目录，入口是 `SKILL.md` 文件。文件以 YAML frontmatter 开头，供 Agent 判断该技能*是否相关*；其后是 Agent *决定使用后*才会阅读的具体指令。

```markdown
---
name: pdf-form-filler
description: 填写、扁平化并从 PDF 表单中提取数据。当用户需要填充 PDF
  模板、读取表单字段或将数据合并进 PDF 文档时使用。
metadata:
  requires:
    bins: ["python3"]
---

# PDF 表单填写

Agent 应遵循的分步指令……

## 脚本
- `scripts/fill.py` —— 根据 JSON 数据文件填充模板。
```

约定：

| 字段 | 必填 | 作用 |
| --- | --- | --- |
| `name` | ✅ | 简短的 kebab-case 标识符，在技能库内唯一。 |
| `description` | ✅ | 一到两句话，必须清晰说明该技能**做什么**以及**何时使用** —— 这是 Agent 进行匹配的依据。 |
| `metadata` | 可选 | 结构化提示，如 `requires.bins`（技能依赖的外部可执行程序）或 `related_skills`（与之配合的技能）。 |

正文应聚焦且可操作。把较长的参考资料、schema 或模板放在技能目录下的独立文件中并以链接引用，使它们仅在真正需要时才被加载。

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/laiye-ai/skills.git
cd skills

# 浏览可用技能
ls skills/
```

要使用某个技能，将你的 Agent 运行时指向 `skills/` 目录（或把单个技能文件夹复制到 Agent 的技能路径）。Agent 会索引每个技能的 `name` 与 `description`，并在任务匹配时加载完整的 `SKILL.md`。

## 新增技能

1. 在 `skills/<your-skill-name>/` 下创建新文件夹。
2. 添加一个带有合法 frontmatter（`name`、`description`）的 `SKILL.md`。
3. 将所需的辅助脚本或资源放在一起。
4. 测试 Agent 能否发现并运行它。
5. 提交 Pull Request。

完整清单见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 开源协议

基于 [MIT License](./LICENSE) 发布。© 2026 来也科技 (Laiye)。
