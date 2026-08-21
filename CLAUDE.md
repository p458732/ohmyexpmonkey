# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Install / dev loop

```bash
pip install -e .
# auto-cd shell wrapper + zsh native completion (em-init.sh is installed into bin/)
source em-init.sh
```

There is no unit-test framework, no linter, and no build step. Verification is
`bash tests/integration.sh`, which drives the real CLI against throwaway git
repos under /tmp; run it after every change. Beyond that, iteration is: edit
`expmonkey/__init__.py`, re-run `em <subcmd>` in a real em project. The editable
install means edits take effect immediately.

Two console entry points (`setup.py`) both map to `expmonkey.main`: `em` (short, recommended) and `expmonkey` (the binary the shell wrapper actually calls).

**The distribution name is `ohmyexpmonkey`; the import name stays `expmonkey`.**
That split is deliberate — training scripts written for classic expmonkey do
`from expmonkey import get_branch`, and renaming the package would break every
one of them. Repo: https://github.com/p458732/ohmyexpmonkey

## Big picture

The whole CLI is one file: `expmonkey/__init__.py` (~1000 lines). `build_parser()` near the bottom is the index — each `sub.add_parser(...)` points at a `cmd_*` function; start there when adding/changing subcommands.

The tool is a thin layer over `git worktree`. The mental model is:

- **basedir** = the directory containing `.em/` (found by walking up from cwd via `find_basedir`).
- **basedir is not a worktree of any branch.** `cmd_init` builds the real repository at `.em/repo` and parks its HEAD on the empty orphan branch `__empty` (`EMPTY_ROOT_BRANCH`, already in `_NON_EXP_BRANCHES`), so nothing is checked out at the root and no `.gitignore` is needed there. `_git_cwd()` resolves every git call to `.em/repo`; `_hooks_dir` goes through `git rev-parse --git-common-dir`, so one hook install covers all worktrees. `em init` takes a required ssh/https URL (`REPO_URL_RE`), refuses a non-empty directory or an existing git repo, and `fetch`es so remote branches are forkable immediately. `EM_ALLOW_LOCAL_URL=1` relaxes the URL check for tests.
- **Series** = a plain local folder whose name starts with `series.` (`SERIES_DIR_PREFIX`); `is_series_dir` is the whole definition. It has no branch, no worktree and no metadata, is never committed or pushed, and may be nested. `em series new` just mkdirs it and copies the root agent files in (`_copy_shared_root_files`, seeded by `ROOT_TEMPLATE_FILES` at init), so a series and its `CLAUDE.md` / `MEMORY.md` are local-only and do not survive a move to another machine — a deliberate trade-off, see the spec. Because it is only a folder, a `mv` would strand the experiment worktrees inside it: `_repair_worktrees` runs `git worktree repair` before every prune (and in `cmd_ls`) to undo that.
- **Experiment** = regular branch with a worktree. **The branch name never contains the folder it sits in**: it is `<user>.<YYMMDD>.<desc>` whether it lives at the project root or nested several series deep, so moving the directory never invalidates the branch. Only the path changes.
- `determine_target_series(basedir, cwd, src_branch)` decides which folder a new experiment lands in, and is now purely cwd-driven (the source branch carries no series prefix to follow). This is the cwd-sensitivity documented in README's "em cp 放置规则" subsection (section 二) — preserve it when touching `cmd_cp`. README is the human-facing source of truth for these rules; keep it in sync with the code.
- **`em cp` has three extra behaviors beyond placement** (all documented in README's cp subsection, keep them in sync): (1) `_resolve_src` resolves the `<src>` arg — `.` means the current branch, a bare name inside a series is tried as `<series>/<name>`, `master` falls back to `main` then `<remote>/master|main`, and any other unmatched name falls back to `<remote>/<src>` (so a freshly inited project, whose only refs are remote ones, can `em cp <remote-branch> <new-branch>` straight away). (2) `cmd_cp` calls `_auto_cm_if_dirty` on the src worktree before forking, so a dirty src is auto-committed (`wip <date> (auto by em cp)`) and the fork captures a complete snapshot. (3) **`em cp <src>` with no `<name>` (or `<name> == <src>`) adopts instead of forking** — `_adopt_branch` checks the branch out under its own name tracking the remote, which is the only way to pick an existing branch back up; forking always restamps the name with the current user and today's date. The completion (`scripts/em-completion.zsh`, `cp` case) offers local *and* remote branches for that reason, and drops `master`.
- **Experiment naming is controlled by `EXP_NAME_PREFIX`** at the top of the file, applied in `_exp_full_name`. It is `"{user}.{date}."` (against the six-digit `EXP_DATE_FMT`), so `em cp dev_gaze my_exp` → branch `viktor.260818.my_exp`. There is no index: names are expected to be unique per user per day, and a clash just fails with `branch already exists`. Set the prefix to `""` for verbatim names; `strip_exp_prefix` is then skipped so a typed name is never silently rewritten. `DATE_FMT` stays eight-digit and is only used for `wip <date>` commit messages.
- Because the user name is the first segment of every experiment name, `_validate_user` rejects one containing `.` (it would make `<user>.<date>.<name>` unparseable) or equal to `series` (it would produce dirs that look like series folders), pointing at `em config user`.
- Experiment **detection** never depended on naming — `_worktree_exps` reads `git worktree list` — which is why classic free-form names still work across `ls` / `cd` / `rm` / `push`. It returns each experiment's **parent path relative to basedir** (`None` at the root), which is what makes arbitrarily nested series folders work.

## Command set

Nine top-level commands: `init`, `series` (new/ls/rm), `cp`, `ls`, `cd`, `rm`,
`push`, `hooks install`, `config` (`user`, `ai_commit`). Five were deliberately retired — don't
reintroduce them without a reason:

- `new` / `cm` were aliases (`em cp master <n>`, `em push -m`).
- `mv` renamed a branch bypassing `EXP_NAME_PREFIX`; `git branch -m` +
  `git worktree move` covers the rare case.
- `group` became redundant once `_repair_worktrees` existed: moving an
  experiment into a series folder is a plain `mv`, healed on the next command.
- `empty` was the only orphan-branch path and went unused.
- `co` folded into `em cp`'s single-argument form (see `_adopt_branch`).

## Commit messages are generated, but never at the cost of a push

`em push` without `-m` calls `_ai_commit_message()`, which pipes the prompt,
`git diff --cached --name-status` and a bounded read of the staged patch into
the command from `commit_msg_cmd` (default `claude -p`). It returns None for
every reason there might not be a message and `_cm_push` then commits
`wip <date>` — see its docstring; that fallback is the feature, so keep it
total when editing.

What the code does not say:

- **On unless switched off.** `_config_off` treats `off/false/0/no/n` as off and
  anything else as on. Generous on the off side on purpose: this switch decides
  whether the staged diff leaves the machine, so a value em fails to recognise
  must not leave it running.
- **Both escapes must keep working** — `em config ai_commit off` and a local
  model via `commit_msg_cmd`. The latter is a config key rather than env-only
  precisely so it survives a new shell; an escape hatch you must re-export in
  every terminal is not one.
- **`_ENV_OVERRIDABLE` is a list, not a rule.** Only those keys read `EM_<KEY>`.
  A blanket rule would let a future key named `branch` hijack `EM_BRANCH`, which
  already means something else, and would leave `EM_AI_COMMIT` appearing nowhere
  in the source.
- **No test may reach a real API.** `tests/integration.sh` unsets
  `EM_COMMIT_MSG_CMD` at script level and pins it to `false` inside `run_em`
  with `${VAR-false}` — the `-` matters, since a test passing an empty value is
  how it reaches the config path instead.
- **`_auto_cm_if_dirty` (`em cp`) and `_backup_before_remove` (`em rm`) are
  excluded.** They are safety snapshots, not curated work; a generator that
  hangs must never sit between the user and a backup-before-delete. `_cm_push`
  is the only commit site that generates, and it has exactly one caller.

Shelling out is why this costs no dependency — no new import was needed, so the
stdlib-only rule below still holds.

## Shared ignore list

`cmd_init` and `cmd_hooks_install` write `expmonkey/gitignore.template` into
`.git/info/exclude` via `_install_gitignore`. It goes there, not into a root
`.gitignore`, because the project root is not a worktree — a file there is
invisible to git — while `info/exclude` sits in the common dir and so covers
every experiment worktree at once, untracked, without showing up as a change
inside any experiment.

The template is a **data file, not a string constant**: it is ~130 patterns and
belongs nowhere near the Python. `setup.py` lists it in `package_data`, or a
non-editable install ships without it; `_install_gitignore` returns None rather
than raising if it is missing, so a broken install cannot stop `em init`.

Rewrites replace only the block between `_EXCLUDE_BEGIN` and `_EXCLUDE_END`;
anything the user wrote outside it survives untouched.

## Removal backs up first, and is local-only

`em rm` and `em series rm` call `_backup_before_remove()` before deleting
anything. `_unpushed_reason()` decides whether the worktree holds something the
remote lacks (uncommitted changes, untracked files, unpushed commits, never
pushed); if so it commits and pushes, and if that push fails the caller
**aborts without removing anything** — deleting after a failed backup would
lose exactly what the backup protects. `em series rm` backs up every nested
experiment before removing any of them, so a failure leaves the series intact.

**Never `push --force`.** em never rewrites history, so local is only ever
behind, equal to, or ahead of the remote, and ahead fast-forwards. A rejected
push therefore means somebody else pushed to that branch, and forcing would
delete their commits. There is deliberately no flag to skip the backup.

They delete a worktree, its directory and its **local**
branch — never the remote. That is what makes "clear local space, keep the
record" safe: a pushed experiment comes back with `em cp <branch>`. There is
no delete refspec anywhere in the file, and `tests/integration.sh` section 3o
locks that in by diffing the remote's branch list across a removal.

The exception is a series folder itself: it is not version controlled at all,
so `em series rm` destroys its notes permanently. Both commands therefore keep
an interactive confirmation unless `-y` is passed.

## Auto-cd contract

Subcommands that change "where the user should be" (e.g. `cmd_series_new`, `cmd_cp`, `cmd_cd`) call `emit_cd(path)`. That writes `path` into the file named by env var `_EM_OUTPUT_NEW_PWD`; the `em()` shell function in `scripts/em-init.sh` sets this env var to a `mktemp` file and `cd`s to its contents after the Python process exits with code 0.

Any new subcommand that conceptually moves the user must call `emit_cd`. Subcommands that don't need to move should not call it.

## Zsh completion

`scripts/em-completion.zsh` is **pure zsh** — it never invokes Python. It rediscovers basedir via `_em_find_basedir` (same walk-up logic as Python), enumerates series folders with `series.*` globs, reads experiments from `git worktree list` (naming-independent, mirroring `_worktree_exps`), and lists branches with one `git for-each-ref` call. Keep the `series.` prefix and the six-digit experiment pattern in sync with `SERIES_DIR_PREFIX` / `EXP_RE` on the Python side if either changes.

Per-kind candidate coloring is done at the top of the completion file via top-level `list-colors` zstyle patterns matched against the candidate string (series-only, exp-only, and `series/exp`). The `_em_compadd` helper still groups candidates with `-J` for display ordering, but coloring does **not** depend on tag context.

## Username resolution

`.em/config` is a flat `key=value` file read through `_config_get(basedir,
key)` / `_config_set` / `_config_off`. Only keys in `_ENV_OVERRIDABLE` honour an
`EM_<KEY>` environment override; `user` is not one of them, because `get_user()`
reads `EM_USER` itself before `find_basedir()` so it still answers outside a
project.

`get_user()` tries in order: env `EM_USER` → `.em/config` `user=` line →
`os.getlogin()` / `$USER` / `$LOGNAME`. Every result passes through
`_validate_user`, which rejects a name containing `.` or equal to `series`.
The rule itself lives in `user_name_problem()`, which *returns* the problem
instead of dying, so `em init` can re-prompt where other callers just fail.

`cmd_init` calls `_settle_user()` at the end: it resolves a name, pins it into
`.em/config` via `_config_set()`, and when the name is unusable either
prompts (tty) or reports and moves on (not a tty — CI must never block). It
never raises: by that point the project is fully built, so an unusable name is
the *next step*, not a failed init. `cmd_config` validates up front so a typo is
caught when typed rather than at the next experiment.
The username is part of every experiment branch/dir name, so changing this
logic changes naming for all subsequent experiments.

**No third-party imports.** `expmonkey/__init__.py` is standard library only
(`argparse`, `os`, `re`, `shutil`, `subprocess`, `sys`, `datetime`) and
`setup.py` has no `install_requires` — keep it that way; a test enforces both.
There is deliberately no login-to-abbreviation mapping (that would mean a
dependency), so a `firstname.lastname` login needs a one-off
`em config user <name>`. `cmd_init` reports that as the next step rather than
failing, since the project is fully built by the time the name is looked up.
