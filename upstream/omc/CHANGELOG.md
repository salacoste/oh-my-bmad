# oh-my-claudecode v4.15.2: HUD, hooks, and workflow reliability fixes

## Release Notes

Patch release focused on default HUD correctness, hook timeout reliability, Windows/path handling, and workflow guardrails since v4.15.1.

### Highlights

- Fix fresh/default HUD config so the `focused` preset is applied even when `settings.json` has no `omcHud` key (#3400, fixes #3399).
- Raise and align UserPromptSubmit hook timeout handling so the skill-injector/keyword-detector path fails open before Claude Code discards output (#3398).
- Respect `OMC_STATE_DIR` for learner skill-session state paths (#3397).
- Improve slow team worker startup tolerance and HUD rate-limit detection (#3395, #3392).
- Fix setup legacy hook warnings, keyword detector informational occurrence scanning, quoted keyword exemptions, and Windows hook child-process hiding (#3389, #3386, #3385).
- Improve HUD/model/currency/cwd behavior and Windows path handling (#3375, #3367, #3360, #3359, #3357).
- Support Claude Sonnet 5 defaults and correct model-routing counting in indented code blocks (#3370, #3364).
- Clarify install tracks and update the Discord invite in docs (#3362, #3373).

### Bug Fixes

- Default HUD fallback now routes through the preset merge path (#3400).
- UserPromptSubmit hook timeout budgets now match real cold-start behavior (#3398, #3387).
- Learner state paths honor `OMC_STATE_DIR` (#3397).
- Team startup, HUD rate-limit panes, setup legacy hooks, keyword scanning, standalone hook deployment, and Windows hook process behavior received targeted reliability fixes (#3395, #3392, #3389, #3386, #3385).
- HUD model extraction, enterprise cost display, cwd rendering, and project-memory Windows path normalization were corrected (#3375, #3367, #3360, #3359, #3357).
- Model-routing defaults/counting were updated for Claude Sonnet 5 and indented code blocks (#3370, #3364).

### Documentation

- Clarified release/install tracks and updated community invite links (#3362, #3373).

### Stats

- Covers the dev changes after v4.15.1 through merge commit `38ea6d136fddf7c4af47a7f0533d434d90734145`.
- Includes 20 post-v4.15.1 dev commits, including PRs #3357 through #3400.
