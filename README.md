# githubFixer

Automatically resolves GitHub issues using a multi-agent Claude pipeline. Point it at a repo, and it analyzes each issue, writes a spec, plans tasks, codes the fix, runs tests, opens a PR, and tracks everything in Linear.

## Prerequisites

- Python 3.9+
- An [Anthropic API key](https://console.anthropic.com/) set as `ANTHROPIC_API_KEY` in your `.env`
- [GitHub CLI (`gh`)](https://cli.github.com/) installed and authenticated (`gh auth login`)
- A [Linear](https://linear.app) workspace with an API key
- (Optional) [Ollama](https://ollama.com) for local LLM cost savings — see [Cost Savings with Ollama](#cost-savings-with-ollama)
- (Optional) [mempalace](https://github.com/milla-jovovich/mempalace) for persistent cross-run memory — see [Persistent Memory with mempalace](#persistent-memory-with-mempalace)

## Installation

```bash
git clone https://github.com/mando222/githubFixer.git
cd githubFixer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
# Required
LINEAR_API_KEY=lin_api_...
LINEAR_TEAM_ID=your-team-id
ANTHROPIC_API_KEY=sk-ant-...

# Optional — override default models
# CODING_AGENT_MODEL=claude-opus-4-6
# SPEC_WRITER_AGENT_MODEL=claude-opus-4-6

# Optional — concurrency
# MAX_CONCURRENT_ISSUES=3

# Optional — Ollama (see Cost Savings section below)
# USE_OLLAMA_FOR_PLANNER=true
# USE_OLLAMA_FOR_SPEC_REVIEWER=true
# OLLAMA_MODEL=qwen2.5:14b
# OLLAMA_BASE_URL=http://localhost:11434
```

Anthropic authentication is configured via `ANTHROPIC_API_KEY` in your `.env` file.

To find your Linear Team ID: go to Linear → Settings → API → scroll to "Team IDs".

## Usage

```bash
source venv/bin/activate

# Interactive picker — lists open issues, you choose which to solve
python run.py owner/repo

# Solve specific issue numbers
python run.py owner/repo 42
python run.py owner/repo 42 67 100

# Solve all open issues without prompting
python run.py owner/repo --all

# Only consider unassigned issues
python run.py owner/repo --unassigned

# Force-solve an issue even if it's closed or marked won't-implement
python run.py owner/repo 42 --force
python run.py owner/repo --all --force
```

### `--force`

By default the pipeline skips issues whose Linear state is `Cancelled` or `Won't Implement`. Pass `--force` to override that check — the issue is reactivated to *In Progress* and the full pipeline runs regardless of its current state. Useful for revisiting deliberately-closed tickets or retrying a previously-rejected fix.

### Example

```bash
python run.py mando222/my-project --all
python run.py mando222/my-project 42 67 100        # three issues in parallel
python run.py mando222/my-project 55 --force       # reopen and retry a closed issue
```

### Parallel execution

Whether you pass `--all` or explicit issue numbers, all selected issues are dispatched concurrently up to `MAX_CONCURRENT_ISSUES` (default: 3). Each issue gets its own isolated git worktree and independent agent pipeline — they do not share state or block each other.

The pipeline will run fully autonomously for each issue:
1. Analyze the codebase
2. Write and review a spec
3. Break the work into tasks and create Linear sub-issues
4. Implement each task, run tests, and self-correct up to 12 cycles
5. Review the implementation against the spec
6. Open a PR on GitHub
7. Mark the Linear ticket "In Review" with the PR link

## Ollama Integration

Two agents — the **task planner** and the **spec reviewer** — do pure text reasoning with no tool use and can optionally run on a local [Ollama](https://ollama.com) instance. All other agents require tool use (Read, Write, Edit, Bash, etc.) and must use the Anthropic API backend.

| Agent | Default model | Ollama? |
|---|---|---|
| Spec reviewer | Claude Haiku | ✅ Binary APPROVED/NEEDS_REVISION output |
| Task planner | Claude Haiku | ✅ JSON task list output |
| Codebase analyzer | Claude Haiku | ❌ Requires Read/Glob/Grep tools |
| Coder | Claude Sonnet | ❌ Requires Read/Write/Edit/Bash tools |
| Tester | Claude Sonnet | ❌ Requires Bash/Read tools |
| Reviewer | Claude Sonnet | ❌ Requires Bash/Read/Glob/Grep tools |
| GitHub submitter | Claude Haiku | ❌ Requires Bash/Read tools |

### Setup

```bash
# Install Ollama and pull a model
brew install ollama        # or https://ollama.com/download
ollama pull qwen2.5:14b   # recommended — strong JSON/instruction following
ollama serve
```

Add to your `.env`:

```env
USE_OLLAMA_FOR_PLANNER=true
USE_OLLAMA_FOR_SPEC_REVIEWER=true
OLLAMA_MODEL=qwen2.5:14b
```

### Model recommendations

- **`qwen2.5:14b`** — best choice, strong instruction following, reliable JSON output
- **`llama3.1:8b`** — lighter/faster, works well for the spec reviewer
- **`mistral:7b`** — acceptable, may add markdown fences around JSON (handled gracefully)

If Ollama isn't running, the system falls back to Claude automatically within 5 seconds — no workflow interruption.

> **Context length:** The planner receives 3–6K tokens of input. If you hit issues, set `OLLAMA_NUM_CTX=8192` via an Ollama [Modelfile](https://github.com/ollama/ollama/blob/main/docs/modelfile.md).

## Persistent Memory with mempalace

githubFixer can optionally use [mempalace](https://github.com/milla-jovovich/mempalace) to retain knowledge across runs — so agents remember past architectural decisions, avoid repeating failures, and skip redundant codebase analysis on unchanged commits.

| Without memory | With memory |
|---|---|
| Phase 1 re-analyzes the full codebase on every run | Analysis cached by commit hash — instant on repeat runs |
| Spec writer has no context of prior implementations | Prior decisions injected into each spec and coder prompt |
| Blocked issues leave no trace for future runs | Failure patterns recorded for learning |

### Setup (one-time)

```bash
# 1. Install mempalace
pip install git+https://github.com/milla-jovovich/mempalace

# 2. Create the palace directory
python3 -c "from mempalace.config import MempalaceConfig; MempalaceConfig().init()"
# Creates ~/.mempalace/config.json and ~/.mempalace/palace/
```

Add to your `.env`:

```env
MEMPALACE_ENABLED=true
MEMPALACE_PALACE_PATH=~/.mempalace/palace   # default, can omit
```

### What gets stored

| Event | Room | Content |
|---|---|---|
| Phase 2 (analyze) completes | `analysis-cache` | Full analysis, keyed by commit hash |
| Phase 6 (PR submitted) | `implementations` | Issue title, spec excerpt, modified files, PR URL |
| Issue blocked | `failure-patterns` | Block reason, failing test names and errors |

Each repo gets its own **wing** (e.g. `owner-repo`), so memory is always scoped per project.

### Cache invalidation

Analysis cache is keyed on `(repo_slug, git-commit-hash)`. A new commit automatically busts the cache — fresh analysis runs and the result is stored. No manual cache clearing needed.

### Disabling

Set `MEMPALACE_ENABLED=false` (the default) to disable completely. The feature flag is read at startup — no restart required between toggles. All mempalace imports are lazy and guarded, so the system runs identically with or without mempalace installed.
