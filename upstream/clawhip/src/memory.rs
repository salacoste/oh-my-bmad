use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, anyhow};

use crate::Result;
use crate::cli::{MemoryInitArgs, MemoryStatusArgs};

#[derive(Debug, Clone, PartialEq, Eq)]
struct MemoryLayout {
    root: PathBuf,
    project_slug: String,
    channel_slug: Option<String>,
    agent_slug: Option<String>,
    today_slug: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct MemoryInitReport {
    written_files: Vec<PathBuf>,
    skipped_files: Vec<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct MemoryStatusReport {
    layout: MemoryLayout,
    memory_file_exists: bool,
    memory_dir_exists: bool,
    markdown_file_count: usize,
    missing_paths: Vec<PathBuf>,
}

pub fn init(args: MemoryInitArgs) -> Result<()> {
    let force = args.force;
    let layout = MemoryLayout::from_init_args(args)?;
    let report = initialize_layout(&layout, force)?;

    println!(
        "Initialized filesystem-offloaded memory scaffold at {}",
        layout.root.display()
    );
    println!("Project slug: {}", layout.project_slug);
    println!("Today file: {}", layout.daily_file().display());
    println!("Written files: {}", report.written_files.len());
    for path in &report.written_files {
        println!("  wrote {}", display_relative(&layout.root, path));
    }
    if !report.skipped_files.is_empty() {
        println!("Skipped existing files: {}", report.skipped_files.len());
        for path in &report.skipped_files {
            println!("  kept {}", display_relative(&layout.root, path));
        }
    }

    Ok(())
}

pub fn status(args: MemoryStatusArgs) -> Result<()> {
    let layout = MemoryLayout::from_status_args(args)?;
    let report = inspect_layout(&layout)?;

    println!("Memory root: {}", report.layout.root.display());
    println!("MEMORY.md: {}", yes_no(report.memory_file_exists));
    println!("memory/: {}", yes_no(report.memory_dir_exists));
    println!(
        "Markdown files under memory/: {}",
        report.markdown_file_count
    );
    println!("Project shard: {}", report.layout.project_file().display());
    println!("Today shard: {}", report.layout.daily_file().display());
    if let Some(path) = report.layout.channel_file() {
        println!("Channel shard: {}", path.display());
    }
    if let Some(path) = report.layout.agent_file() {
        println!("Agent shard: {}", path.display());
    }

    if report.missing_paths.is_empty() {
        println!("Status: scaffold looks ready");
    } else {
        println!("Missing recommended paths:");
        for path in report.missing_paths {
            println!("  - {}", display_relative(&report.layout.root, &path));
        }
    }

    Ok(())
}

impl MemoryLayout {
    fn from_init_args(args: MemoryInitArgs) -> Result<Self> {
        let MemoryInitArgs {
            root,
            project,
            channel,
            agent,
            date,
            force: _,
        } = args;
        Self::build(root, project, channel, agent, date)
    }

    fn from_status_args(args: MemoryStatusArgs) -> Result<Self> {
        let MemoryStatusArgs {
            root,
            project,
            channel,
            agent,
            date,
        } = args;
        Self::build(root, project, channel, agent, date)
    }

    fn build(
        root: Option<PathBuf>,
        project: Option<String>,
        channel: Option<String>,
        agent: Option<String>,
        date: Option<String>,
    ) -> Result<Self> {
        let root = match root {
            Some(root) => root,
            None => env::current_dir().context("resolve current directory for memory root")?,
        };
        let project = project
            .or_else(|| {
                root.file_name()
                    .map(|name| name.to_string_lossy().into_owned())
            })
            .ok_or_else(|| anyhow!("unable to infer project slug from root path"))?;

        Ok(Self {
            root,
            project_slug: slugify(&project)?,
            channel_slug: channel.map(|value| slugify(&value)).transpose()?,
            agent_slug: agent.map(|value| slugify(&value)).transpose()?,
            today_slug: normalize_date_slug(date)?,
        })
    }

    fn memory_file(&self) -> PathBuf {
        self.root.join("MEMORY.md")
    }

    fn memory_dir(&self) -> PathBuf {
        self.root.join("memory")
    }

    fn memory_index_file(&self) -> PathBuf {
        self.memory_dir().join("README.md")
    }

    fn daily_dir(&self) -> PathBuf {
        self.memory_dir().join("daily")
    }

    fn daily_file(&self) -> PathBuf {
        self.daily_dir().join(format!("{}.md", self.today_slug))
    }

    fn projects_dir(&self) -> PathBuf {
        self.memory_dir().join("projects")
    }

    fn project_file(&self) -> PathBuf {
        self.projects_dir()
            .join(format!("{}.md", self.project_slug))
    }

    fn channels_dir(&self) -> PathBuf {
        self.memory_dir().join("channels")
    }

    fn channel_file(&self) -> Option<PathBuf> {
        self.channel_slug
            .as_ref()
            .map(|slug| self.channels_dir().join(format!("{slug}.md")))
    }

    fn agents_dir(&self) -> PathBuf {
        self.memory_dir().join("agents")
    }

    fn agent_file(&self) -> Option<PathBuf> {
        self.agent_slug
            .as_ref()
            .map(|slug| self.agents_dir().join(format!("{slug}.md")))
    }

    fn topics_dir(&self) -> PathBuf {
        self.memory_dir().join("topics")
    }

    fn rules_file(&self) -> PathBuf {
        self.topics_dir().join("rules.md")
    }

    fn lessons_file(&self) -> PathBuf {
        self.topics_dir().join("lessons.md")
    }

    fn handoffs_dir(&self) -> PathBuf {
        self.memory_dir().join("handoffs")
    }

    fn archive_dir(&self) -> PathBuf {
        self.memory_dir().join("archive")
    }

    fn expected_dirs(&self) -> Vec<PathBuf> {
        vec![
            self.memory_dir(),
            self.daily_dir(),
            self.projects_dir(),
            self.channels_dir(),
            self.agents_dir(),
            self.topics_dir(),
            self.handoffs_dir(),
            self.archive_dir(),
        ]
    }

    fn expected_files(&self) -> Vec<PathBuf> {
        let mut files = vec![
            self.memory_file(),
            self.memory_index_file(),
            self.daily_file(),
            self.project_file(),
            self.rules_file(),
            self.lessons_file(),
            self.handoffs_dir().join(".gitkeep"),
            self.archive_dir().join(".gitkeep"),
        ];
        if let Some(path) = self.channel_file() {
            files.push(path);
        }
        if let Some(path) = self.agent_file() {
            files.push(path);
        }
        files
    }
}

fn initialize_layout(layout: &MemoryLayout, force: bool) -> Result<MemoryInitReport> {
    for dir in layout.expected_dirs() {
        fs::create_dir_all(&dir)
            .with_context(|| format!("create memory directory {}", dir.display()))?;
    }

    let mut written_files = Vec::new();
    let mut skipped_files = Vec::new();
    for (path, contents) in scaffold_files(layout) {
        if write_scaffold_file(&path, &contents, force)? {
            written_files.push(path);
        } else {
            skipped_files.push(path);
        }
    }

    Ok(MemoryInitReport {
        written_files,
        skipped_files,
    })
}

fn inspect_layout(layout: &MemoryLayout) -> Result<MemoryStatusReport> {
    let memory_dir = layout.memory_dir();
    let markdown_file_count = count_markdown_files(&memory_dir)?;

    let mut missing_paths = Vec::new();
    for path in layout.expected_dirs() {
        if !path.is_dir() {
            missing_paths.push(path);
        }
    }
    for path in layout.expected_files() {
        if !path.exists() {
            missing_paths.push(path);
        }
    }

    Ok(MemoryStatusReport {
        layout: layout.clone(),
        memory_file_exists: layout.memory_file().is_file(),
        memory_dir_exists: memory_dir.is_dir(),
        markdown_file_count,
        missing_paths,
    })
}

fn scaffold_files(layout: &MemoryLayout) -> Vec<(PathBuf, String)> {
    let mut files = vec![
        (layout.memory_file(), render_memory_md(layout)),
        (layout.memory_index_file(), render_memory_index(layout)),
        (layout.daily_file(), render_daily_file(layout)),
        (layout.project_file(), render_project_file(layout)),
        (layout.rules_file(), render_rules_file()),
        (layout.lessons_file(), render_lessons_file()),
        (
            layout.handoffs_dir().join(".gitkeep"),
            String::from("# tracked by clawhip memory init\n"),
        ),
        (
            layout.archive_dir().join(".gitkeep"),
            String::from("# tracked by clawhip memory init\n"),
        ),
    ];

    if let Some(path) = layout.channel_file() {
        files.push((path, render_channel_file(layout)));
    }
    if let Some(path) = layout.agent_file() {
        files.push((path, render_agent_file(layout)));
    }

    files
}

fn write_scaffold_file(path: &Path, contents: &str, force: bool) -> Result<bool> {
    if path.exists() && !force {
        return Ok(false);
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("create parent directory {}", parent.display()))?;
    }
    fs::write(path, contents).with_context(|| format!("write scaffold file {}", path.display()))?;
    Ok(true)
}

fn render_memory_md(layout: &MemoryLayout) -> String {
    let mut quick_map = vec![
        format!(
            "- Project status: `memory/projects/{}.md`",
            layout.project_slug
        ),
        format!(
            "- Today's execution log: `memory/daily/{}.md`",
            layout.today_slug
        ),
        String::from("- Durable rules: `memory/topics/rules.md`"),
        String::from("- Durable lessons: `memory/topics/lessons.md`"),
        String::from("- Full subtree guide: `memory/README.md`"),
    ];
    if let Some(channel) = &layout.channel_slug {
        quick_map.insert(
            2,
            format!("- Channel state: `memory/channels/{channel}.md`"),
        );
    }
    if let Some(agent) = &layout.agent_slug {
        quick_map.insert(3, format!("- Agent profile: `memory/agents/{agent}.md`"));
    }

    let mut read_when = vec![
        format!(
            "- You need repo/project status -> read `memory/projects/{}.md`",
            layout.project_slug
        ),
        String::from("- You need latest execution context -> read today's file in `memory/daily/`"),
        String::from("- You are changing workflow policy -> read `memory/topics/rules.md`"),
    ];
    if let Some(channel) = &layout.channel_slug {
        read_when.insert(
            2,
            format!("- You are acting in one channel/lane -> read `memory/channels/{channel}.md`"),
        );
    }

    format!(
        "# MEMORY.md — pointer/index layer

## Current beliefs

- Current priority: keep the hot memory layer small and push durable detail into `memory/`.
- Root memory is for summaries, pointers, and write obligations only.
- Detailed logs belong in `memory/`.

## Quick file map

{}

## Read this when...

{}

## Write obligations

- Daily progress goes to `memory/daily/{}.md`.
- Project-specific detail goes to `memory/projects/{}.md`.
- Durable lessons get promoted into `memory/topics/lessons.md`.
- `MEMORY.md` only changes when the pointer map or current beliefs change.
",
        quick_map.join("\n"),
        read_when.join("\n"),
        layout.today_slug,
        layout.project_slug,
    )
}

fn render_memory_index(layout: &MemoryLayout) -> String {
    let mut file_map = vec![
        String::from("- `daily/YYYY-MM-DD.md` -> chronological work log"),
        format!(
            "- `projects/{}.md` -> canonical repo/project state",
            layout.project_slug
        ),
        String::from("- `topics/rules.md` -> durable operating rules"),
        String::from("- `topics/lessons.md` -> reusable lessons"),
        String::from("- `handoffs/YYYY-MM-DD-<slug>.md` -> bounded handoffs"),
        String::from("- `archive/YYYY-MM/` -> cold history"),
    ];
    if let Some(channel) = &layout.channel_slug {
        file_map.insert(1, format!("- `channels/{channel}.md` -> one lane/channel"));
    } else {
        file_map.insert(
            1,
            String::from("- `channels/<channel>.md` -> one lane/channel"),
        );
    }
    if let Some(agent) = &layout.agent_slug {
        file_map.insert(
            2,
            format!("- `agents/{agent}.md` -> one agent/operator profile"),
        );
    } else {
        file_map.insert(
            2,
            String::from("- `agents/<agent>.md` -> one agent/operator profile"),
        );
    }

    format!(
        "# memory/README.md — retrieval guide

## File map

{}

## Read by situation

- Need latest execution context -> latest file in `daily/`
- Need canonical project state -> `projects/{}.md`
- Need policy or norms -> `topics/rules.md`

## Naming rules

- Use stable slugs for channels, projects, and agents.
- Keep `MEMORY.md` short; move durable detail into leaf files.
- Archive inactive history instead of bloating the hot path.
",
        file_map.join("\n"),
        layout.project_slug,
    )
}

fn render_daily_file(layout: &MemoryLayout) -> String {
    let mut lines = vec![
        format!("# {}", layout.today_slug),
        String::new(),
        "## Summary".into(),
        String::new(),
        format!("- Active project: `{}`", layout.project_slug),
        "- Use this file for chronological execution notes and short checkpoints.".into(),
    ];
    if let Some(channel) = &layout.channel_slug {
        lines.push(format!(
            "- Active channel lane: `memory/channels/{channel}.md`"
        ));
    }
    if let Some(agent) = &layout.agent_slug {
        lines.push(format!(
            "- Active agent profile: `memory/agents/{agent}.md`"
        ));
    }
    lines.extend([
        String::new(),
        "## Log".into(),
        String::new(),
        "- Scaffold created with `clawhip memory init`.".into(),
    ]);
    lines.join("\n") + "\n"
}

fn render_project_file(layout: &MemoryLayout) -> String {
    format!(
        "# {}

## Current state

- Canonical project shard for repo-specific status.
- Use this file for active plans, blockers, decisions, and durable context.

## Keep here

- project status
- active priorities
- blockers and follow-ups
- links to handoffs and decisions
",
        layout.project_slug
    )
}

fn render_channel_file(layout: &MemoryLayout) -> String {
    let channel = layout
        .channel_slug
        .as_deref()
        .expect("channel file only rendered when channel slug exists");
    format!(
        "# {}

## Role

- Canonical memory for one channel or workflow lane.
- Keep local context, commitments, and lane-specific follow-ups here.

## Related shards

- project state -> `memory/projects/{}.md`
- daily execution log -> `memory/daily/{}.md`
- durable rules -> `memory/topics/rules.md`
",
        channel, layout.project_slug, layout.today_slug
    )
}

fn render_agent_file(layout: &MemoryLayout) -> String {
    let agent = layout
        .agent_slug
        .as_deref()
        .expect("agent file only rendered when agent slug exists");
    format!(
        "# {}

## Role

- Canonical memory for one agent or operator profile.
- Keep preferences, handoff expectations, and recurring operating notes here.

## Related shards

- project state -> `memory/projects/{}.md`
- daily execution log -> `memory/daily/{}.md`
- durable lessons -> `memory/topics/lessons.md`
",
        agent, layout.project_slug, layout.today_slug
    )
}

fn render_rules_file() -> String {
    String::from(
        "# rules

- Root `MEMORY.md` stays short and skimmable.
- Durable workflow rules live here, not in the daily log.
- Refactor noisy memory into dedicated shards instead of growing one hot file.
",
    )
}

fn render_lessons_file() -> String {
    String::from(
        "# lessons

- Promote reusable lessons here after they become stable.
- Keep one lesson per bullet or subsection so agents can scan quickly.
",
    )
}

fn count_markdown_files(root: &Path) -> Result<usize> {
    if !root.exists() {
        return Ok(0);
    }

    let mut count = 0;
    for entry in fs::read_dir(root).with_context(|| format!("read {}", root.display()))? {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            count += count_markdown_files(&path)?;
        } else if path.extension().is_some_and(|ext| ext == "md") {
            count += 1;
        }
    }
    Ok(count)
}

fn slugify(input: &str) -> Result<String> {
    let mut slug = String::new();
    let mut last_was_dash = false;

    for ch in input.trim().chars() {
        let normalized = match ch {
            'a'..='z' | '0'..='9' => Some(ch),
            'A'..='Z' => Some(ch.to_ascii_lowercase()),
            ' ' | '_' | '-' | '/' | '.' => Some('-'),
            _ => None,
        };

        let Some(ch) = normalized else {
            continue;
        };

        if ch == '-' {
            if slug.is_empty() || last_was_dash {
                continue;
            }
            last_was_dash = true;
            slug.push(ch);
        } else {
            last_was_dash = false;
            slug.push(ch);
        }
    }

    while slug.ends_with('-') {
        slug.pop();
    }

    if slug.is_empty() {
        Err(anyhow!("unable to derive a stable slug from '{input}'").into())
    } else {
        Ok(slug)
    }
}

fn normalize_date_slug(date: Option<String>) -> Result<String> {
    match date {
        Some(date) => {
            let trimmed = date.trim();
            if is_valid_date_slug(trimmed) {
                Ok(trimmed.to_string())
            } else {
                Err(anyhow!("date must use YYYY-MM-DD format").into())
            }
        }
        None => Ok(today_slug()),
    }
}

fn is_valid_date_slug(value: &str) -> bool {
    value.len() == 10
        && value.as_bytes()[4] == b'-'
        && value.as_bytes()[7] == b'-'
        && value
            .bytes()
            .enumerate()
            .all(|(index, byte)| matches!(index, 4 | 7) || byte.is_ascii_digit())
}

fn today_slug() -> String {
    let date = time::OffsetDateTime::now_utc().date();
    format!(
        "{:04}-{:02}-{:02}",
        date.year(),
        u8::from(date.month()),
        date.day()
    )
}

fn yes_no(value: bool) -> &'static str {
    if value { "yes" } else { "no" }
}

fn display_relative(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .display()
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn init_creates_memory_scaffold() {
        let tempdir = tempfile::tempdir().expect("tempdir");
        let layout = MemoryLayout {
            root: tempdir.path().to_path_buf(),
            project_slug: "clawhip".into(),
            channel_slug: Some("alerts".into()),
            agent_slug: Some("codex".into()),
            today_slug: "2026-03-10".into(),
        };

        let report = initialize_layout(&layout, false).expect("initialize layout");

        assert!(report.written_files.contains(&layout.memory_file()));
        assert!(layout.memory_file().is_file());
        assert!(layout.memory_index_file().is_file());
        assert!(layout.project_file().is_file());
        assert!(layout.daily_file().is_file());
        assert!(layout.rules_file().is_file());
        assert!(layout.lessons_file().is_file());
        assert!(layout.channel_file().expect("channel").is_file());
        assert!(layout.agent_file().expect("agent").is_file());
        assert!(layout.handoffs_dir().join(".gitkeep").is_file());
        assert!(layout.archive_dir().join(".gitkeep").is_file());

        let memory_md = fs::read_to_string(layout.memory_file()).expect("read MEMORY.md");
        assert!(memory_md.contains("memory/projects/clawhip.md"));
        assert!(memory_md.contains("memory/channels/alerts.md"));
        assert!(memory_md.contains("memory/agents/codex.md"));
    }

    #[test]
    fn init_keeps_existing_files_without_force() {
        let tempdir = tempfile::tempdir().expect("tempdir");
        let layout = MemoryLayout {
            root: tempdir.path().to_path_buf(),
            project_slug: "clawhip".into(),
            channel_slug: None,
            agent_slug: None,
            today_slug: "2026-03-10".into(),
        };

        fs::create_dir_all(layout.memory_dir()).expect("create memory dir");
        fs::write(layout.memory_file(), "custom memory").expect("write existing memory file");

        let report = initialize_layout(&layout, false).expect("initialize layout");

        assert!(report.skipped_files.contains(&layout.memory_file()));
        assert_eq!(
            fs::read_to_string(layout.memory_file()).expect("read MEMORY.md"),
            "custom memory"
        );
    }

    #[test]
    fn inspect_reports_missing_recommended_paths() {
        let tempdir = tempfile::tempdir().expect("tempdir");
        let layout = MemoryLayout {
            root: tempdir.path().to_path_buf(),
            project_slug: "clawhip".into(),
            channel_slug: Some("alerts".into()),
            agent_slug: None,
            today_slug: "2026-03-10".into(),
        };

        fs::create_dir_all(layout.memory_dir()).expect("create memory dir");

        let report = inspect_layout(&layout).expect("inspect layout");

        assert!(report.memory_dir_exists);
        assert!(!report.memory_file_exists);
        assert!(report.missing_paths.contains(&layout.memory_file()));
        assert!(report.missing_paths.contains(&layout.project_file()));
        assert!(
            report
                .missing_paths
                .contains(&layout.channel_file().expect("channel"))
        );
    }

    #[test]
    fn slugify_normalizes_common_inputs() {
        assert_eq!(
            slugify("Clawhip Workspace").expect("slug"),
            "clawhip-workspace"
        );
        assert_eq!(
            slugify("issue_73/runtime").expect("slug"),
            "issue-73-runtime"
        );
    }

    #[test]
    fn slugify_rejects_empty_results() {
        let error = slugify("!!!").expect_err("invalid slug should fail");
        assert!(error.to_string().contains("stable slug"));
    }

    #[test]
    fn normalize_date_slug_accepts_iso_dates() {
        assert_eq!(
            normalize_date_slug(Some("2026-03-10".into())).expect("date slug"),
            "2026-03-10"
        );
    }

    #[test]
    fn normalize_date_slug_rejects_non_iso_dates() {
        let error = normalize_date_slug(Some("03/10/2026".into())).expect_err("invalid date");
        assert!(error.to_string().contains("YYYY-MM-DD"));
    }
}
