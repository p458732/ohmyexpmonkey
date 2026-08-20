#!/usr/bin/env python3
"""expmonkey - branch-based experiment management with series grouping."""

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import date

__version__ = "0.1.0"

EM_DIR = ".em"
DATE_FMT = "%Y%m%d"

# Seeded blank at init; em series new copies them into every series folder.
# They sit at the project root, which is not a worktree, so they stay local.
ROOT_TEMPLATE_FILES = ("CLAUDE.md", "MEMORY.md")
# A series is nothing but a folder whose name starts with this. No branch, no
# worktree, no metadata: the prefix is the whole definition.
SERIES_DIR_PREFIX = "series."
EXP_RE = re.compile(r"^([^\W\d_]\w*)\.(\d{6})\.(.+)$")

# Experiment dir/branch name = EXP_NAME_PREFIX + the typed name.
# Fields: {user}, {date} (YYMMDD via EXP_DATE_FMT). Set to "" for verbatim names.
EXP_NAME_PREFIX = "{user}.{date}."

# Six-digit date used only in experiment names; DATE_FMT stays eight-digit and
# is only used for "wip <date>" commit messages.
EXP_DATE_FMT = "%y%m%d"

# Would produce dirs like series.260818.foo, which the series folder prefix
# rule would misread as a series.
RESERVED_USER_NAMES = {"series"}

# Branch parked in .em/repo so the project root checks out nothing.
EMPTY_ROOT_BRANCH = "__empty"

# `em init` takes a remote git URL only: ssh (git@host:path, ssh://) or https.
# Set EM_ALLOW_LOCAL_URL=1 to also accept a local path (used by the test script).
REPO_URL_RE = re.compile(r"^(?:https://|ssh://|git://|[\w.\-]+@[\w.\-]+:).+")

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_C_RESET = "\033[0m"
_C_SERIES = "\033[1;36m"   # bold cyan
_C_EXP = "\033[33m"        # yellow
_C_USER = "\033[1;32m"     # bold green
_C_DIM = "\033[2m"


def cs(s):
    return f"{_C_SERIES}{s}{_C_RESET}" if _USE_COLOR else s


def cu(s):
    return f"{_C_USER}{s}{_C_RESET}" if _USE_COLOR else s


def ce(s):
    return f"{_C_EXP}{s}{_C_RESET}" if _USE_COLOR else s


def cb(branch):
    """Color a branch name, splitting on '/' for series/exp distinction."""
    if "/" in branch:
        s, e = branch.split("/", 1)
        return f"{cs(s)}/{ce(e)}"
    return ce(branch)


def die(msg, code=1):
    print(f"em: {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg):
    print(f"em: {msg}", file=sys.stderr)


def run(args, cwd=None, check=True, capture=True):
    res = subprocess.run(args, cwd=cwd, capture_output=capture, text=True)
    if check and res.returncode != 0:
        err = (res.stderr or "").strip() if capture else ""
        die(f"command failed: {' '.join(args)}\n{err}")
    return (res.stdout or "").strip() if capture else ""


def today_str():
    return date.today().strftime(DATE_FMT)


def exp_date_str():
    return date.today().strftime(EXP_DATE_FMT)


def find_basedir(start=None):
    p = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(p, EM_DIR)):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            die("not in an em project; run `em init` first")
        p = parent


def is_series_dir(name):
    """True if a single path segment names a series folder."""
    return name.startswith(SERIES_DIR_PREFIX)


def normalize_series_name(name):
    """Accept `test` or `series.test`; return `series.test`."""
    name = name.strip("/")
    if not name or os.sep in name or name in (".", ".."):
        die(f"invalid series name: {name!r}; use a single path segment")
    return name if is_series_dir(name) else SERIES_DIR_PREFIX + name


def parse_exp(name):
    """Return (user, date, name) for <user>.<YYMMDD>.<name>."""
    m = EXP_RE.match(name)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def strip_exp_prefix(name):
    """Strip optional <series>/ and any <user>.<date>. prefix, return the name."""
    if "/" in name:
        head, tail = name.split("/", 1)
        if is_series_dir(head):
            name = tail
    m = parse_exp(name)
    if m:
        return m[2]
    return name


def current_series(cwd=None):
    """Path (relative to basedir) of the series folders containing cwd, or None.
    Supports nesting: inside series.a/series.b returns "series.a/series.b".
    """
    basedir = find_basedir()
    cwd = os.path.abspath(cwd or os.getcwd())
    rel = os.path.relpath(cwd, basedir)
    if rel == "." or rel.startswith(".."):
        return None
    head = []
    for part in rel.split(os.sep):
        if not is_series_dir(part):
            break
        head.append(part)
    return os.sep.join(head) if head else None


def current_exp(cwd=None):
    """Return (series_or_None, exp_dirname) if cwd is inside an experiment
    worktree, else None. Detection is worktree-based (not name-pattern based),
    so classic expmonkey free-form experiments are recognized too."""
    basedir = find_basedir()
    cwd = os.path.abspath(cwd or os.getcwd())
    rel = os.path.relpath(cwd, basedir)
    if rel == "." or rel.startswith(".."):
        return None
    parts = rel.split(os.sep)
    exps = _worktree_exps(basedir)
    # nested: <folders...>/<exp>[/...] -- try the deepest split first
    for i in range(len(parts) - 1, 0, -1):
        series = os.sep.join(parts[:i])
        if any(s == series and d == parts[i] for s, d, _b in exps):
            return series, parts[i]
    # at the project root: <exp>[/...]
    if any(s is None and d == parts[0] for s, d, _b in exps):
        return None, parts[0]
    return None


def get_basedir(path=None):
    """Public API: return the basedir of the em project containing `path` (or cwd)."""
    return find_basedir(path)


def get_branch(path=None):
    """Public API for user code (training scripts etc.): return the current
    experiment branch name.

    Resolution order:
      1. env var EM_BRANCH or EXPMONKEY_BRANCH (override, for batch runs that
         pass branch in; the EXPMONKEY_ name is kept for expmonkey compat)
      2. `git symbolic-ref --short HEAD` of the worktree containing `path`
         (or cwd)

    Raises RuntimeError if not on a branch, or if the current branch isn't an
    experiment branch (i.e. it's master/main or the empty root branch).
    """
    env = os.environ.get("EM_BRANCH") or os.environ.get("EXPMONKEY_BRANCH")
    if env:
        return env
    cwd = os.path.abspath(path or os.getcwd())
    find_basedir(cwd)  # verify we're in an em project
    res = subprocess.run(
        ["git", "-C", cwd, "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"not on a branch (detached HEAD?) at {cwd}")
    branch = res.stdout.strip()
    if branch in _NON_EXP_BRANCHES:
        raise RuntimeError(
            f"current branch {branch!r} is not an experiment branch"
        )
    return branch


def get_repo_name(path=None):
    """Public API (expmonkey-compat): the experiment repo's name.

    Resolution order:
      1. env var EXPMONKEY_REPO_NAME (override)
      2. basename of the first git remote's URL, minus a trailing '.git'
      3. the basedir directory name (when there is no remote)

    Provided so training scripts written for classic expmonkey
    (`from expmonkey import get_repo_name`) keep working under this version.
    """
    env = os.environ.get("EXPMONKEY_REPO_NAME")
    if env:
        return env
    basedir = find_basedir(path)
    gcwd = _git_cwd(basedir)
    remotes = run(["git", "remote"], cwd=gcwd).split()
    if remotes:
        url = run(["git", "remote", "get-url", remotes[0]], cwd=gcwd)
        name = os.path.basename(url.rstrip("/"))
        if name.endswith(".git"):
            name = name[:-4]
        if name:
            return name
    return os.path.basename(os.path.abspath(basedir))


def user_name_problem(name):
    """Why `name` can't be an experiment name's first segment, or None if it can.

    Returned rather than raised so `em init` can re-prompt instead of dying.
    """
    if not name:
        return "empty"
    if "." in name:
        return (f"{name!r} contains '.', which makes <user>.<date>.<name> "
                f"impossible to parse back")
    if name.lower() in RESERVED_USER_NAMES:
        return f"{name!r} is reserved (it would look like a series folder)"
    return None


def _validate_user(name):
    problem = user_name_problem(name)
    if problem:
        die(f"user name {problem}; run `em config user <name>`")
    return name


def write_user_config(basedir, name):
    """Persist `user=<name>` into .em/config, replacing any existing line."""
    cfg = os.path.join(basedir, EM_DIR, "config")
    lines = []
    if os.path.isfile(cfg):
        lines = [l for l in open(cfg).read().splitlines() if not l.startswith("user=")]
    lines.append(f"user={name}")
    with open(cfg, "w") as f:
        f.write("\n".join(lines) + "\n")


def get_user():
    v = os.environ.get("EM_USER")
    if v:
        return _validate_user(v)
    basedir = find_basedir()
    cfg = os.path.join(basedir, EM_DIR, "config")
    if os.path.isfile(cfg):
        for line in open(cfg):
            line = line.strip()
            if line.startswith("user="):
                return _validate_user(line.split("=", 1)[1])
    try:
        login = os.getlogin()
    except OSError:
        login = os.environ.get("USER") or os.environ.get("LOGNAME")
    if login:
        return _validate_user(login)
    die("cannot determine user; set EM_USER or write `user=<name>` to .em/config")


def _git_cwd(basedir):
    """Return the real git repository root for git operations.

    Supports two layouts:
      - Standard:  basedir/.git is the repo        -> return basedir
      - .em/repo:  basedir/.em/repo/.git is real   -> return basedir/.em/repo
    """
    em_repo = os.path.join(basedir, EM_DIR, "repo")
    if os.path.isdir(os.path.join(em_repo, ".git")):
        return em_repo
    return basedir


def _repair_worktrees(basedir):
    """Re-link worktrees whose directory was moved.

    git stores absolute paths in both directions, so a plain `mv` leaves a
    worktree registered at its old path and marked prunable; the next prune
    would then orphan it. Repairing first makes moving a series folder safe.
    """
    paths = []
    for root, dirs, files in os.walk(basedir):
        dirs[:] = [d for d in dirs if d not in (EM_DIR, ".git")]
        if ".git" in files:            # a linked worktree's .git is a FILE
            paths.append(root)
            dirs[:] = []               # never walk into a worktree's contents
    if paths:
        run(["git", "worktree", "repair"] + paths,
            cwd=_git_cwd(basedir), check=False)


def list_branches(basedir):
    out = run(["git", "branch", "--format=%(refname:short)"], cwd=_git_cwd(basedir))
    return [b.strip() for b in out.splitlines() if b.strip()]


def list_worktrees(basedir):
    out = run(["git", "worktree", "list", "--porcelain"], cwd=_git_cwd(basedir))
    result, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                result.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur["path"] = line.split(" ", 1)[1]
        elif line.startswith("HEAD "):
            cur["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            ref = line.split(" ", 1)[1]
            if ref.startswith("refs/heads/"):
                ref = ref[len("refs/heads/"):]
            cur["branch"] = ref
        elif line.startswith("prunable"):
            cur["prunable"] = True
        elif line.startswith("locked"):
            cur["locked"] = True
    return result


_NON_EXP_BRANCHES = {"master", "main", "__empty"}


def _worktree_exps(basedir):
    """All experiment worktrees, independent of naming convention, so classic
    expmonkey free-form names (e.g. `hzw.from_lb.dit.baseline`) are recognized
    alongside mecha `<user>.<date>.<desc>` names. Returns a list of
    (parent_or_None, dirname, branch), where parent is the experiment's parent
    path relative to basedir (None at the root). Excludes master/main/__empty
    and the nested .em/repo worktree."""
    base = os.path.abspath(basedir)
    out = []
    for wt in list_worktrees(basedir):
        path, branch = wt.get("path"), wt.get("branch")
        if not path or not branch:
            continue
        if branch in _NON_EXP_BRANCHES:
            continue
        # skip broken/stale worktrees (directory deleted outside em): they can't
        # be cd'd, grouped or removed, and would otherwise pollute listings.
        if wt.get("prunable"):
            continue
        ap = os.path.abspath(path)
        if not os.path.isdir(ap):
            continue
        parent = os.path.dirname(ap)
        name = os.path.basename(ap)
        if parent == base:
            out.append((None, name, branch))            # experiment at the root
        elif parent.startswith(base + os.sep):
            rel_parent = os.path.relpath(parent, base)
            if rel_parent.split(os.sep)[0] == EM_DIR:   # the .em/repo worktree
                continue
            out.append((rel_parent, name, branch))      # nested in folders
    return out


def emit_cd(path):
    out = os.environ.get("_EM_OUTPUT_NEW_PWD")
    if out:
        with open(out, "w") as f:
            f.write(path)
    else:
        info(f"to cd: cd {path}  (source em-init.sh for auto-cd)")


def determine_target_series(basedir, cwd, src_branch=None):
    """Return the folder (relative to basedir) a new experiment goes into,
    or None for the project root. Purely cwd-driven: the source branch no
    longer carries a series prefix, so it cannot influence placement.
    """
    del basedir, src_branch  # kept for call-site compatibility
    return current_series(cwd)


# ---------- git hooks ----------

LARGE_FILE_LIMIT_MIB = 60

_PRE_COMMIT_MARKER = "# managed by em (large-file pre-commit check)"

_PRE_COMMIT_HOOK = rf"""#!/usr/bin/env bash
{_PRE_COMMIT_MARKER}
# Reject commits that introduce files larger than the size limit below.
# Bypass once:   git commit --no-verify
# Reinstall:     em hooks install
# Uninstall:     rm "$(git rev-parse --git-common-dir)/hooks/pre-commit"

limit_mib={LARGE_FILE_LIMIT_MIB}
limit=$(( limit_mib * 1024 * 1024 ))
fail=0
errors=""

while IFS= read -r -d '' path; do
    sha=$(git ls-files -s -- "$path" | awk '{{print $2}}')
    [ -z "$sha" ] && continue
    size=$(git cat-file -s "$sha" 2>/dev/null) || continue
    if [ "$size" -gt "$limit" ]; then
        human=$(awk -v s="$size" 'BEGIN{{printf "%.1f", s/1048576}}')
        errors+="  $path  (${{human}}MiB)"$'\n'
        fail=1
    fi
done < <(git diff --cached --name-only --diff-filter=ACMR -z)

if [ "$fail" -ne 0 ]; then
    echo "em pre-commit: refusing to commit, files exceed ${{limit_mib}}MiB limit:" >&2
    printf '%s' "$errors" >&2
    echo "" >&2
    echo "options:" >&2
    echo "  - unstage:     git restore --staged <file>" >&2
    echo "  - use git-lfs or external storage for big artifacts" >&2
    echo "  - bypass once: git commit --no-verify" >&2
    exit 1
fi
exit 0
"""


_EXCLUDE_BEGIN = "# ── managed by em: regenerated by `em hooks install` ──"
_EXCLUDE_END = "# ── end managed by em ──"


def _install_gitignore(basedir):
    """Install em's ignore list into .git/info/exclude.

    Not a .gitignore at the project root: the root is not a worktree, so a file
    there is invisible to git. info/exclude lives in the common dir, so one copy
    covers every experiment worktree, and being untracked it never shows up as a
    change inside an experiment.

    Rules outside em's marked block are left exactly as they are — the file is
    where a user puts their own rules too, so this only ever replaces its own
    block. Returns the number of patterns installed, or None if the template is
    missing (a broken install must not stop `em init`).
    """
    template = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "gitignore.template")
    if not os.path.isfile(template):
        return None
    with open(template, encoding="utf-8") as f:
        body = f.read().strip("\n")

    gd = run(["git", "rev-parse", "--git-common-dir"], cwd=_git_cwd(basedir))
    if not os.path.isabs(gd):
        gd = os.path.join(_git_cwd(basedir), gd)
    info_dir = os.path.join(gd, "info")
    os.makedirs(info_dir, exist_ok=True)
    path = os.path.join(info_dir, "exclude")

    existing = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    kept = existing
    if _EXCLUDE_BEGIN in existing and _EXCLUDE_END in existing:
        head, rest = existing.split(_EXCLUDE_BEGIN, 1)
        _old, tail = rest.split(_EXCLUDE_END, 1)
        kept = head.rstrip("\n") + tail
    block = f"{_EXCLUDE_BEGIN}\n{body}\n{_EXCLUDE_END}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(block + ("\n" + kept.strip("\n") + "\n" if kept.strip() else ""))
    return len([l for l in body.splitlines() if l.strip() and not l.startswith("#")])


def _hooks_dir(basedir):
    git_cwd = _git_cwd(basedir)
    gd = run(["git", "rev-parse", "--git-common-dir"], cwd=git_cwd)
    if not os.path.isabs(gd):
        gd = os.path.join(git_cwd, gd)
    return os.path.join(gd, "hooks")


def _install_pre_commit_hook(basedir):
    hooks_dir = _hooks_dir(basedir)
    os.makedirs(hooks_dir, exist_ok=True)
    target = os.path.join(hooks_dir, "pre-commit")
    if os.path.isfile(target):
        existing = open(target).read()
        if _PRE_COMMIT_MARKER not in existing:
            backup = target + ".bak"
            shutil.move(target, backup)
            info(f"backed up existing pre-commit hook to {backup}")
    with open(target, "w") as f:
        f.write(_PRE_COMMIT_HOOK)
    os.chmod(target, 0o755)
    return target


# ---------- commands ----------

def _detect_user():
    """The name em would use if nothing were configured: EM_USER, else login."""
    v = os.environ.get("EM_USER")
    if v:
        return v
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get("USER") or os.environ.get("LOGNAME") or ""


def _settle_user(basedir):
    """Pin a usable user name into .em/config, asking for one if need be.

    Called only by `em init`, once the project exists. Pinning makes naming
    deterministic for the project: logging in as someone else, or moving the
    folder to another machine, no longer changes what new experiments are called.
    Returns the name, or None if none could be settled (never raises: the
    project is already built, so this must not turn init into a failure).
    """
    name = _detect_user()
    problem = user_name_problem(name)
    if not problem:
        write_user_config(basedir, name)
        return name
    info(f"cannot use {problem}")
    # Non-interactive (CI, scripts, pipes): report and move on rather than block.
    if not sys.stdin.isatty():
        return None
    for _ in range(3):
        try:
            entered = input("  user name for this project "
                            "(letters/digits/_/-, no dots; Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not entered:
            return None
        problem = user_name_problem(entered)
        if problem:
            info(f"cannot use {problem}")
            continue
        write_user_config(basedir, entered)
        return entered
    return None


def _is_repo_url(url):
    if os.environ.get("EM_ALLOW_LOCAL_URL") == "1":
        return True
    return bool(REPO_URL_RE.match(url))


def cmd_init(args):
    cwd = os.path.abspath(os.getcwd())
    if os.path.isdir(os.path.join(cwd, EM_DIR)):
        die("already an em project")
    if os.path.exists(os.path.join(cwd, ".git")):
        die(f"already a git repo: {cwd}\n"
            "em init builds its own repo under .em/repo; run it in a fresh empty directory")
    if os.listdir(cwd):
        die(f"directory is not empty: {cwd}\n"
            "the em project root holds nothing but experiment worktrees")
    if not _is_repo_url(args.repo):
        die(f"not a git URL: {args.repo}\n"
            "usage: em init <ssh-or-https-git-url>\n"
            "  e.g. em init git@github.com:p458732/VeCoR.git")

    # The project root is deliberately NOT a worktree of any branch: the real
    # repository lives in .em/repo (which _git_cwd resolves to), and every
    # directory under the root is an experiment worktree. Nothing is checked out
    # at the root, so no .gitignore is needed to keep it clean.
    em_repo = os.path.join(cwd, EM_DIR, "repo")
    run(["git", "init", em_repo])
    run(["git", "remote", "add", "origin", args.repo], cwd=em_repo)
    # Park HEAD on an empty orphan branch so .em/repo itself checks out nothing.
    # EMPTY_ROOT_BRANCH is in _NON_EXP_BRANCHES, so it never shows up as an experiment.
    run(["git", "symbolic-ref", "HEAD", f"refs/heads/{EMPTY_ROOT_BRANCH}"], cwd=em_repo)
    run(["git", "commit", "--allow-empty", "-m", "em: empty root"], cwd=em_repo)
    # Fetch so remote branches are forkable straight away (em cp <remote-branch>).
    run(["git", "fetch", "origin"], cwd=em_repo, capture=False)

    # Blank templates every series folder inherits a copy of. Edit them here to
    # change what future series start with; existing series keep their snapshot.
    for fname in ROOT_TEMPLATE_FILES:
        p = os.path.join(cwd, fname)
        if not os.path.exists(p):
            open(p, "w").close()

    _install_pre_commit_hook(cwd)
    _install_gitignore(cwd)
    print(f"initialized em project at {cwd}")
    print(f"  remote: {args.repo}")
    user = _settle_user(cwd)
    if user:
        print(f"  user:   {cu(user)}")
        print("  next:   em cp <remote-branch> <new-branch>")
    else:
        print(f"  user:   {cu('(not set)')}")
        print("  next:   em config user <name>, then em cp <remote-branch> <new-branch>")


def _copied_banner(rel_src, series_name):
    """Top-of-file note marking a .md as a per-series copy and where it came from.
    The HTML comment is machine-readable (em-copied-from: <relpath>); the blockquote
    is a human-facing GitHub admonition."""
    return (
        f"<!-- em-copied-from: {rel_src} -->\n"
        f"> [!NOTE]\n"
        f"> 📋 Copied into series `{series_name}` from `{rel_src}` on {date.today().isoformat()}.\n"
        f"> Edits here are local to this series and do **not** affect the original.\n\n"
    )


# Agent-tool config that should follow a topic into each series. Markdown
# (CLAUDE.md, AGENTS.md, ...) is matched by extension; these are the extra
# non-.md entries to carry along: agent dirs and opencode's JSON config.
SHARED_ROOT_DIRS = (".claude", ".agent", ".opencode")
SHARED_ROOT_FILES = ("opencode.json", "opencode.jsonc")


def _copy_shared_root_files(basedir, series_dir, series_name):
    """Copy basedir-level agent config (*.md, opencode.json[c], .claude/.agent/
    .opencode) into series_dir. Copies (not symlinks) so editing a series copy
    can never touch the root originals; each series owns an independent snapshot
    in its own git history. Copied .md files get an origin banner; JSON config is
    copied verbatim (a banner would break JSON parsing)."""
    copied = []
    for entry in sorted(os.listdir(basedir)):
        is_md = entry.lower().endswith(".md")
        if not (is_md or entry in SHARED_ROOT_DIRS or entry in SHARED_ROOT_FILES):
            continue
        src = os.path.join(basedir, entry)
        dst = os.path.join(series_dir, entry)
        if os.path.lexists(dst):
            continue
        if os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=True)
        elif is_md:
            rel_src = os.path.relpath(src, series_dir)  # e.g. ../CLAUDE.md, survives moves
            try:
                with open(src, "r", encoding="utf-8") as f:
                    body = f.read()
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(_copied_banner(rel_src, series_name) + body)
            except (UnicodeDecodeError, OSError):
                shutil.copy2(src, dst)  # non-text or unreadable: copy verbatim
        else:
            shutil.copy2(src, dst)  # config file (e.g. opencode.json): verbatim, no banner
        copied.append(entry)
    return copied


def cmd_series_new(args):
    basedir = find_basedir()
    cwd = os.path.abspath(os.getcwd())
    rel = os.path.relpath(cwd, basedir)
    if rel.startswith(".."):
        die("run em series new inside the em project")
    name = normalize_series_name(args.topic)
    series_dir = os.path.join(cwd, name)
    if os.path.exists(series_dir):
        die(f"already exists: {os.path.relpath(series_dir, basedir)}")
    os.makedirs(series_dir)
    copied = _copy_shared_root_files(basedir, series_dir, name)
    print(f"created series: {cs(os.path.relpath(series_dir, basedir))}")
    if copied:
        print(f"copied from root: {', '.join(copied)}")
    emit_cd(series_dir)


def _all_series_dirs(basedir):
    """Every series folder under basedir, as paths relative to basedir."""
    out = []
    for root, dirs, _files in os.walk(basedir):
        dirs[:] = [d for d in dirs if d != EM_DIR and not d.startswith(".")]
        for d in list(dirs):
            if is_series_dir(d):
                out.append(os.path.relpath(os.path.join(root, d), basedir))
            else:
                dirs.remove(d)      # never descend into experiments
    return sorted(out)


def cmd_series_ls(_args):
    basedir = find_basedir()
    for name in _all_series_dirs(basedir):
        print(cs(name))


def cmd_series_rm(args):
    basedir = find_basedir()
    name = normalize_series_name(args.name)
    matches = [s for s in _all_series_dirs(basedir)
               if s == name or s.endswith(os.sep + name)]
    if not matches:
        die(f"no such series: {name}")
    if len(matches) > 1:
        die("ambiguous, multiple matches:\n  " + "\n  ".join(matches))
    rel = matches[0]
    path = os.path.join(basedir, rel)
    nested = [(s, d, b) for s, d, b in _worktree_exps(basedir)
              if s == rel or (s or "").startswith(rel + os.sep)]
    if not args.yes:
        extra = f" and its {len(nested)} experiment(s)" if nested else ""
        ans = input(f"remove series {rel}{extra} at {path}? "
                    f"(nothing here is version controlled) [y/N] ")
        if ans.strip().lower() != "y":
            return
    # Back every nested experiment up first, and abort the whole removal if any
    # of them cannot be: a partial delete is worse than none.
    for s, d, branch in nested:
        wt = os.path.join(basedir, s, d)
        problem = _backup_before_remove(basedir, wt, branch, f"experiment {ce(d)}")
        if problem:
            die(f"{problem}\n  series {rel} was left untouched")
    gcwd = _git_cwd(basedir)
    # Local only, like em rm: the nested experiments lose their worktree and
    # local branch, but anything pushed stays on the remote. The folder itself
    # and its notes are never version controlled, so those are gone for good.
    for s, d, branch in nested:
        wt = os.path.join(basedir, s, d)
        run(["git", "worktree", "remove", "--force", wt], cwd=gcwd, check=False)
        run(["git", "branch", "-D", branch], cwd=gcwd, check=False)
    if os.path.isdir(path):
        shutil.rmtree(path)
    _repair_worktrees(basedir)
    run(["git", "worktree", "prune"], cwd=gcwd, check=False)
    print(f"removed series: {cs(rel)}")
    if nested:
        note = (f"  {len(nested)} experiment(s) removed locally; "
                f"pushed copies stay on the remote")
        print(f"{_C_DIM}{note}{_C_RESET}" if _USE_COLOR else note)


def _exp_full_name(user, day, name):
    """The experiment's dir/branch name: EXP_NAME_PREFIX + the typed name."""
    if not EXP_NAME_PREFIX:
        return name
    return EXP_NAME_PREFIX.format(user=user, date=day) + name


def _create_exp(basedir, target_series, user, expname, src_ref):
    """Create a new experiment worktree.

    target_series is the folder (relative to basedir) to put it in, or None for
    the project root. It decides the PATH only -- the branch name never carries
    the folder, so the same branch keeps its name wherever it is moved to.
    """
    # With a verbatim naming scheme the typed name IS the name; stripping a
    # <user>.<date>. prefix off it would silently rewrite what was asked for.
    name = strip_exp_prefix(expname) if EXP_NAME_PREFIX else expname
    if not re.match(r"^\w[\w\-.]*$", name):
        die(f"invalid experiment name {name!r}; allowed: word chars + . - (Unicode ok)")
    day = exp_date_str()
    branch = _exp_full_name(user, day, name)
    path = (os.path.join(basedir, target_series, branch) if target_series
            else os.path.join(basedir, branch))
    if branch in list_branches(basedir):
        die(f"branch already exists: {branch}")
    if os.path.exists(path):
        die(f"path already exists: {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    run(["git", "worktree", "add", "-b", branch, path, src_ref], cwd=_git_cwd(basedir))
    return branch, path


def _ref_exists(basedir, ref):
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=_git_cwd(basedir), capture_output=True,
    ).returncode == 0


def _resolve_src(basedir, src, default_series):
    """Resolve src arg to a valid branch name (flat or series-qualified).

    For src == "master", fall back to "main" or "<remote>/master" / "<remote>/main"
    if a local master isn't present (covers fresh clones and main-default repos).
    """
    if src == ".":
        src = get_branch()
    branches = list_branches(basedir)
    if src in branches:
        return src
    if "/" not in src and default_series:
        candidate = f"{default_series}/{src}"
        if candidate in branches:
            return candidate
    if src == "master":
        if "master" in branches:
            return "master"
        if "main" in branches:
            return "main"
        remotes = run(["git", "remote"], cwd=_git_cwd(basedir)).split()
        for r in remotes:
            for name in ("master", "main"):
                ref = f"{r}/{name}"
                if _ref_exists(basedir, ref):
                    return ref
        die("no master/main branch found (locally or on any remote)")
    # Any other name: fall back to a remote-tracking branch, so a freshly inited
    # project — whose only refs are remote ones — can fork straight from the remote.
    for r in run(["git", "remote"], cwd=_git_cwd(basedir)).split():
        ref = f"{r}/{src}"
        if _ref_exists(basedir, ref):
            return ref
    die(f"source branch not found: {src}")


def _find_worktree_path(basedir, branch):
    for wt in list_worktrees(basedir):
        if wt.get("branch") == branch:
            return wt.get("path")
    return None


def _branch_at_path(basedir, path):
    """The branch actually checked out at worktree `path`. Needed because
    `em group` relocates a worktree without renaming its branch, so the branch
    can differ from the path-derived name."""
    ap = os.path.abspath(path)
    for wt in list_worktrees(basedir):
        if os.path.abspath(wt.get("path", "")) == ap:
            return wt.get("branch")
    return None


def _auto_cm_if_dirty(path, label):
    """git add -A + commit if there's anything to commit. No-op if clean."""
    run(["git", "add", "-A"], cwd=path)
    clean = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=path
    ).returncode == 0
    if clean:
        return
    msg = f"wip {today_str()} (auto by em cp)"
    run(["git", "commit", "-m", msg], cwd=path)
    info(f"{label}: auto-committed pending changes")


def _adopt_branch(basedir, branch):
    """Check out <branch> under the current folder under its own name.

    Unlike forking, this keeps the branch's identity — same name, tracking the
    remote — so commits go back to the same branch. That is the only way to
    pick a branch back up: forking always restamps the name with your user and
    today's date, producing a different branch.
    """
    remotes = run(["git", "remote"], cwd=_git_cwd(basedir)).split()
    if remotes:
        run(["git", "fetch", remotes[0]], cwd=_git_cwd(basedir), capture=False)
    series = current_series()
    path = (os.path.join(basedir, series, branch) if series
            else os.path.join(basedir, branch))
    if os.path.isdir(path):
        die(f"already checked out: {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _repair_worktrees(basedir)
    run(["git", "worktree", "prune"], cwd=_git_cwd(basedir), check=False)
    if branch in list_branches(basedir):
        run(["git", "worktree", "add", path, branch], cwd=_git_cwd(basedir))
    elif remotes and _ref_exists(basedir, f"{remotes[0]}/{branch}"):
        ref = f"{remotes[0]}/{branch}"
        run(["git", "worktree", "add", "-B", branch, path, ref], cwd=_git_cwd(basedir))
    else:
        die(f"branch not found: {branch}")
    print(f"checked out: {cb(branch)} at {os.path.relpath(path, basedir)}")
    emit_cd(path)


def cmd_cp(args):
    basedir = find_basedir()
    # `em cp <src>` (or `em cp <src> <src>`) takes the branch as it is; giving a
    # different <name> forks it into a new experiment of your own.
    if args.name is None or args.name == args.src:
        _adopt_branch(basedir, args.src)
        return
    user = get_user()
    src_ref = _resolve_src(basedir, args.src, current_series())
    # If src has a checked-out worktree with pending changes, commit them first
    # so the fork starts from a complete snapshot rather than the last commit.
    src_wt = _find_worktree_path(basedir, src_ref)
    if src_wt:
        _auto_cm_if_dirty(src_wt, f"src {cb(src_ref)}")
    cwd = os.path.abspath(os.getcwd())
    target_series = determine_target_series(basedir, cwd, src_ref)
    branch, path = _create_exp(basedir, target_series, user, args.name, src_ref)
    print(f"created experiment: {cb(branch)} (from {cb(src_ref)})")
    emit_cd(path)


def _unpushed_reason(basedir, path, branch):
    """Why this worktree holds something the remote does not, or None if it is
    fully backed up. Cheap and local: no fetch, because the push that follows
    is what actually proves the remote is up to date."""
    if subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=path).returncode != 0:
        return "uncommitted changes"
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=path)
    if untracked:
        return "untracked files"
    if not branch:
        return None
    remotes = run(["git", "remote"], cwd=_git_cwd(basedir)).split()
    if not remotes:
        return "no remote to back up to"
    ref = f"{remotes[0]}/{branch}"
    if not _ref_exists(basedir, ref):
        return "never pushed"
    ahead = run(["git", "rev-list", "--count", f"{ref}..HEAD"], cwd=path, check=False)
    return "unpushed commits" if ahead not in ("", "0") else None


def _backup_before_remove(basedir, path, branch, label):
    """Push whatever the remote does not have, so removing is never destructive.

    Returns None once the work is safe, or a message explaining why it is not.
    Callers must abort on a message: deleting after a failed backup would lose
    exactly what the backup exists to protect.
    """
    reason = _unpushed_reason(basedir, path, branch)
    if reason is None:
        return None
    if reason == "no remote to back up to":
        return (f"{label} has work that is not backed up and there is no remote "
                f"to push to; add one, or move the directory aside by hand")
    info(f"{label}: {reason} -- backing up before removal")
    remotes = run(["git", "remote"], cwd=_git_cwd(basedir)).split()
    run(["git", "add", "-A"], cwd=path)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=path).returncode != 0:
        run(["git", "commit", "-m", f"backup before em rm {today_str()}"], cwd=path)
    # Never --force: a rejected push means the branch diverged, i.e. somebody
    # else pushed to it, and forcing would delete their commits.
    res = subprocess.run(["git", "push", remotes[0], branch],
                         cwd=path, capture_output=True, text=True)
    if res.returncode != 0:
        detail = (res.stderr or "").strip().splitlines()
        return (f"could not back {label} up: "
                f"{detail[-1] if detail else 'push failed'}\n"
                f"  the branch has diverged from the remote, or the remote is "
                f"unreachable. Nothing was removed. Resolve the branch (e.g. "
                f"`git -C {path} pull --rebase`) and try again.")
    print(f"backed up {cb(branch)} to {remotes[0]}")
    return None


def _exp_path(basedir, series, expname):
    return os.path.join(basedir, series, expname) if series else os.path.join(basedir, expname)


def cmd_ls(args):
    basedir = find_basedir()
    # Listing is the natural thing to run after moving folders around, so let
    # it be the cheap place where a moved worktree gets re-linked.
    _repair_worktrees(basedir)
    series = current_series()
    # Experiments are detected via worktrees (naming-independent), so classic
    # expmonkey free-form experiments are listed too.
    by_series = {}
    for s, name, _b in _worktree_exps(basedir):
        by_series.setdefault(s, []).append(name)
    if args.all or not series:
        all_series = sorted(set(_all_series_dirs(basedir)) |
                            {s for s in by_series if s})
        for s in all_series:
            print(cs(s))
            for name in sorted(by_series.get(s, [])):
                print(f"  {ce(name)}")
        for name in sorted(by_series.get(None, [])):
            print(ce(name))
    else:
        for name in sorted(by_series.get(series, [])):
            print(ce(name))


def cmd_cd(args):
    basedir = find_basedir()
    name = args.name
    if "/" in name:
        target = os.path.join(basedir, name)
    else:
        matches = []
        # a series folder, given as `test` or `series.test`
        for rel in _all_series_dirs(basedir):
            base = os.path.basename(rel)
            if base in (name, SERIES_DIR_PREFIX + name):
                matches.append(os.path.join(basedir, rel))
        # an experiment with this dir name (flat or series-nested), worktree-based
        # so classic free-form names match too
        for s, d, _b in _worktree_exps(basedir):
            if d == name:
                matches.append(_exp_path(basedir, s, d))
        matches = sorted(set(matches))
        if len(matches) == 0:
            die(f"not found: {name}")
        if len(matches) > 1:
            die("ambiguous, multiple matches:\n  " + "\n  ".join(matches))
        target = matches[0]
    if not os.path.isdir(target):
        die(f"not a directory: {target}")
    emit_cd(target)


def cmd_rm(args):
    basedir = find_basedir()
    name = args.name
    if is_series_dir(os.path.basename(name.rstrip("/"))):
        die("em rm only removes experiments; use `em series rm` for a series folder")
    # Resolve which experiment worktree to remove (path), naming-independent.
    if "/" in name:
        cand = os.path.join(basedir, name)
        path = cand if os.path.isdir(cand) else None
    else:
        cur = current_series()
        candidates = []
        for s, d, _b in _worktree_exps(basedir):
            if d == name and (s is None or s == cur):
                candidates.append(_exp_path(basedir, s, d))
        candidates = sorted(set(candidates))
        if len(candidates) > 1:
            die("ambiguous; specify <series>/<exp>:\n  " + "\n  ".join(candidates))
        path = candidates[0] if candidates else None
    if not path or not os.path.isdir(path):
        die(f"no such experiment: {name}")
    # Look up the REAL branch at this path: `em group` relocates a worktree
    # without renaming its branch, so the branch may differ from the path.
    branch = _branch_at_path(basedir, path)
    label = os.path.relpath(path, basedir)
    if not args.yes:
        ans = input(f"remove experiment {label} at {path}? [y/N] ")
        if ans.strip().lower() != "y":
            return
    problem = _backup_before_remove(basedir, path, branch, f"experiment {ce(label)}")
    if problem:
        die(problem)
    gcwd = _git_cwd(basedir)
    run(["git", "worktree", "remove", "--force", path], cwd=gcwd, check=False)
    if os.path.isdir(path):
        shutil.rmtree(path)
    if branch:
        run(["git", "branch", "-D", branch], cwd=gcwd, check=False)
    _repair_worktrees(basedir)
    run(["git", "worktree", "prune"], cwd=gcwd, check=False)
    # Local only, by design: whatever was pushed stays on the remote, so
    # clearing disk space is reversible via `em cp <branch>`.
    print(f"removed locally: {cb(branch or label)}")
    if branch:
        print(f"  {_C_DIM}pushed copies stay on the remote; "
              f"`em cp {branch}` brings it back{_C_RESET}" if _USE_COLOR
              else f"  pushed copies stay on the remote; "
                   f"`em cp {branch}` brings it back")


def _cm_push(path, message, label):
    """cm+push the worktree at path. Returns True if pushed."""
    run(["git", "add", "-A"], cwd=path)
    clean = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=path
    ).returncode == 0
    if not clean:
        msg = message or f"wip {today_str()}"
        run(["git", "commit", "-m", msg], cwd=path)
        info(f"{label}: committed")
    else:
        info(f"{label}: nothing to commit")
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    remotes = run(["git", "remote"], cwd=path).split()
    if not remotes:
        info(f"{label}: no remote; skipping push")
        return False
    run(["git", "push", "-u", remotes[0], branch], cwd=path, capture=False)
    print(f"pushed {label}: {cb(branch)}")
    return True


def cmd_push(args):
    basedir = find_basedir()
    exp = current_exp()
    if not exp:
        die("not inside an experiment")
    series, expname = exp
    # Only the experiment is pushed: a series is a local folder with no branch.
    exp_path = _exp_path(basedir, series, expname)
    _cm_push(exp_path, args.message, f"experiment {ce(expname)}")


def cmd_hooks_install(_args):
    """(Re)apply em's git-level setup: the pre-commit hook and the ignore list.
    Both live in the shared common dir, so this covers every worktree at once."""
    basedir = find_basedir()
    target = _install_pre_commit_hook(basedir)
    print(f"installed pre-commit hook: {target}")
    print(f"  rejects commits containing files larger than {LARGE_FILE_LIMIT_MIB}MiB")
    count = _install_gitignore(basedir)
    if count:
        print(f"refreshed ignore list: {count} patterns in .git/info/exclude")


def cmd_config(args):
    basedir = find_basedir()
    if args.key == "user" and args.value:
        # Reject here, not at the next em command: the mistake is visible now.
        _validate_user(args.value)
        write_user_config(basedir, args.value)
        print(f"set user={args.value}")
    else:
        die("usage: em config user <name>")


def build_parser():
    p = argparse.ArgumentParser(prog="em", description="branch-based experiment management (expmonkey)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="initialize an em project in cwd from a git URL")
    sp.add_argument("repo", help="git URL (ssh or https) of the experiment repo")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("series", help="manage series folders (local only)")
    ssub = sp.add_subparsers(dest="series_cmd", required=True)
    sn = ssub.add_parser("new", help="create a new series folder")
    sn.add_argument("topic", help="series name (the series. prefix is added if missing)")
    sn.set_defaults(func=cmd_series_new)
    sl = ssub.add_parser("ls", help="list series folders")
    sl.set_defaults(func=cmd_series_ls)
    sr = ssub.add_parser("rm", help="remove a series folder and its experiments")
    sr.add_argument("name")
    sr.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    sr.set_defaults(func=cmd_series_rm)

    sp = sub.add_parser("cp", help="fork an experiment, or take a branch as it is")
    sp.add_argument("src", help="branch to start from")
    sp.add_argument("name", nargs="?", default=None,
                    help="name for the new experiment; omit (or repeat <src>) to "
                         "check out <src> itself under its own name")
    sp.set_defaults(func=cmd_cp)

    sp = sub.add_parser("ls", help="list experiments")
    sp.add_argument("-a", "--all", action="store_true")
    sp.set_defaults(func=cmd_ls)

    sp = sub.add_parser("cd", help="cd to experiment or series")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_cd)

    sp = sub.add_parser("rm", help="remove an experiment")
    sp.add_argument("name")
    sp.add_argument("-y", "--yes", action="store_true")
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("push", help="commit and push current experiment")
    sp.add_argument("-m", "--message", default=None)
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("hooks", help="manage git hooks for this em project")
    hsub = sp.add_subparsers(dest="hooks_cmd", required=True)
    hi = hsub.add_parser("install", help=f"install pre-commit hook (rejects files >{LARGE_FILE_LIMIT_MIB}MiB)")
    hi.set_defaults(func=cmd_hooks_install)

    sp = sub.add_parser("config", help="set local config (user)")
    sp.add_argument("key")
    sp.add_argument("value", nargs="?")
    sp.set_defaults(func=cmd_config)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
