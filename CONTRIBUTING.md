# Contributing to Laiye Agent Skills

Thanks for your interest in contributing! This repository collects reusable
*Agent Skills*. Each skill lives in its own folder under [`skills/`](./skills).

## Adding a new skill

1. **Create a folder** under `skills/<your-skill-name>/`. Use a short,
   descriptive, kebab-case name.
2. **Add a `SKILL.md`** with YAML frontmatter at the top:

   ```markdown
   ---
   name: your-skill-name
   description: What the skill does and when an agent should use it.
   ---

   # Your Skill

   Clear, step-by-step instructions...
   ```

3. **Bundle resources** (scripts, templates, reference docs) inside the same
   folder. Reference long material by link so it loads only when needed.
4. **Test discovery and execution** — make sure an agent can find the skill by
   its description and run any scripts it ships.
5. **Open a pull request** describing what the skill does.

## Skill quality checklist

- [ ] `name` is unique within the library and matches the folder name.
- [ ] `description` clearly states **what** the skill does and **when** to use it.
- [ ] The body is focused and actionable; long references are split into separate files.
- [ ] Scripts have no hard-coded secrets and declare their dependencies.
- [ ] Examples or sample inputs are included where helpful.

## Style

- Write instructions for an agent to follow, not prose for a human to admire.
- Prefer explicit steps and concrete commands.
- Keep skills single-purpose; split unrelated capabilities into separate skills.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](./LICENSE).
