# Legacy bridge note

This directory is no longer the public integration surface.

Use the provider-native Codex or Claude hook configuration plus the generic local ingress:

```bash
clawhip native hook --provider codex --file payload.json
clawhip native hook --provider claude --file payload.json
```

Use `.clawhip/hooks/` only for additive augmentation. Routing identity now comes from git
repo/worktree discovery, not repo-local clawhip metadata files.
