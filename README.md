<div align="center">

# Laiye Agent Skills

**An open collection of reusable AI Agent Skills built and maintained by [Laiye (来也科技)](https://laiye.com).**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Skill Format: SKILL.md](https://img.shields.io/badge/format-SKILL.md-blue.svg)](#skill-format)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](./CONTRIBUTING.md)

[English](./README.md) · [中文](./README_zh.md)

</div>

---

## Overview

**Laiye Agent Skills** is a curated, open-source library of *Agent Skills* — self-contained, model-loadable capabilities that extend an AI agent with domain knowledge, procedures, and tools.

Each skill is a folder containing a `SKILL.md` file plus any scripts and resources it needs. An agent discovers skills by their `name` and `description`, then loads the full instructions **on demand** only when a task matches — keeping the base context small while making deep, specialized capabilities available when they're needed.

This repository collects the skills Laiye uses across its products and research, packaged so the broader community can use, adapt, and contribute to them.

## Key Features

- **Progressive disclosure** — Only a short name + description is always loaded; the full skill body is pulled in only when relevant, so many skills can coexist without bloating context.
- **Self-contained** — Each skill bundles its own instructions, scripts, and reference files in a single directory.
- **Model-agnostic** — Skills are plain Markdown + assets. They work with any agent runtime that supports the `SKILL.md` convention.
- **Composable** — Skills can call scripts, reference other skills, and be combined to handle multi-step workflows.
- **Open & extensible** — MIT licensed; adding a new skill is as simple as adding a new folder.

## Repository Structure

```
skills/
├── README.md                    # This file (English)
├── README_zh.md                 # Chinese version
├── LICENSE                      # MIT license
├── CONTRIBUTING.md              # How to add or improve a skill
└── skills/                      # The skill library — one folder per skill
    ├── cashflow-daily-report/   # Pulls bank transactions and renders a daily cash-flow report
    │   ├── SKILL.md
    │   ├── scripts/             # Data-fetch, summarize, and render scripts
    │   ├── references/          # Output formats and detailed guidance
    │   └── data/                # Sample transaction data
    ├── expense-reimbursement/   # End-to-end expense reimbursement automation
    │   ├── SKILL.md
    │   ├── config.json          # Skill configuration
    │   ├── scripts/
    │   └── references/
    └── geo-content-scorer/      # Scores content for citability in AI search engines (GEO)
        ├── SKILL.md
        ├── scripts/
        ├── references/
        └── assets/
```

A skill folder always contains a `SKILL.md`; everything else (`scripts/`,
`references/`, `data/`, `config.json`, …) is optional and loaded on demand.

## Skill Format

A skill is a directory whose entry point is a `SKILL.md` file. The file starts with YAML frontmatter that the agent reads to decide *whether* the skill is relevant, followed by the instructions the agent reads *once it has decided to use it*.

```markdown
---
name: pdf-form-filler
description: Fill, flatten, and extract data from PDF forms. Use when the
  user needs to populate a PDF template, read form fields, or merge data
  into a PDF document.
metadata:
  requires:
    bins: ["python3"]
---

# PDF Form Filler

Step-by-step instructions the agent should follow...

## Scripts
- `scripts/fill.py` — fills a template from a JSON data file.
```

Guidelines:

| Field | Required | Purpose |
| --- | --- | --- |
| `name` | ✅ | Short, kebab-case identifier, unique within the library. |
| `description` | ✅ | One or two sentences. Must clearly state **what** the skill does and **when** to use it — this is what the agent matches against. |
| `metadata` | optional | Structured hints such as `requires.bins` (external binaries the skill needs) or `related_skills` (skills it composes with). |

Keep the body focused and actionable. Put long reference material, schemas, or templates in separate files within the skill folder and link to them, so they load only when actually needed.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/laiye-ai/skills.git
cd skills

# Browse the available skills
ls skills/
```

To use a skill, point your agent runtime at the `skills/` directory (or copy an individual skill folder into your agent's skills path). The agent will index each skill's `name` and `description` and load the full `SKILL.md` when a task matches.

## Adding a Skill

1. Create a new folder under `skills/<your-skill-name>/`.
2. Add a `SKILL.md` with valid frontmatter (`name`, `description`).
3. Put any helper scripts or resources alongside it.
4. Test that an agent can discover and run it.
5. Open a pull request.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full checklist.

## License

Released under the [MIT License](./LICENSE). © 2026 Laiye (来也科技).
