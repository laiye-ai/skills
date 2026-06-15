---
name: hello-skill
description: A minimal example skill that greets a user by name. Use this as a
  reference template when creating new skills, or to verify that your agent
  runtime can discover and run skills in this repository.
---

# Hello Skill

This is a reference skill that demonstrates the structure every skill in this
repository follows: YAML frontmatter (`name`, `description`) followed by
agent-facing instructions, plus an optional `scripts/` directory.

## When to use

Use this skill to:

- Confirm your agent runtime can discover and execute skills.
- Copy it as a starting point for a new skill.

## Steps

1. Determine the name to greet. If the user did not provide one, ask, or
   default to `World`.
2. Run the greeting script:

   ```bash
   python scripts/greet.py "<name>"
   ```

3. Return the script's output to the user.

## Scripts

- `scripts/greet.py` — prints a greeting for the given name.
