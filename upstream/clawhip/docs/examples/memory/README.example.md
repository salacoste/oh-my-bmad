# memory/README.md — retrieval guide

## File map

- `daily/YYYY-MM-DD.md` -> chronological work log
- `channels/<channel>.md` -> per-channel state and commitments
- `projects/<project>.md` -> repo/project-specific status
- `topics/rules.md` -> durable operating rules
- `topics/lessons.md` -> reusable lessons
- `handoffs/YYYY-MM-DD-<slug>.md` -> bounded handoffs
- `archive/YYYY-MM/` -> cold history

## Read by situation

- Need latest execution context -> latest `daily/` file
- Need canonical project state -> matching file in `projects/`
- Need one lane's background -> matching file in `channels/`
- Need policy or norms -> `topics/rules.md`

## Naming rules

- Use stable slugs for channels and projects.
- Use one active daily-file convention.
- Archive inactive time slices instead of bloating hot files.
