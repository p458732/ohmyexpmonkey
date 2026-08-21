#!/bin/bash
# Integration tests for em. No unit-test framework in this project (see
# CLAUDE.md); this drives the real CLI against throwaway git repos.
#   bash tests/integration.sh
EM_SRC="${EM_SRC:-$(cd "$(dirname "$0")/.." && pwd)}"
LAB="${LAB:-/tmp/em-integration}"
PASS=0; FAIL=0

# A maintainer who has EM_COMMIT_MSG_CMD exported (pointing at a real model)
# would otherwise run the whole suite through it. Clearing it here still lets
# 3t's per-call `EM_COMMIT_MSG_CMD=... run_em` prefixes win, since an assignment
# in front of a function call beats a script-level unset.
unset EM_COMMIT_MSG_CMD EM_AI_COMMIT
run_em() { EM_USER=viktor EM_ALLOW_LOCAL_URL=1 PYTHONPATH="$EM_SRC" \
           EM_COMMIT_MSG_CMD="${EM_COMMIT_MSG_CMD-false}" \
           python3 -c "import expmonkey; expmonkey.main()" "$@"; }
G="git -c user.email=a@b -c user.name=a"

ok()   { PASS=$((PASS+1)); echo "  PASS: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL: $1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$3', got '$2')"; fi; }

# ---------- fixture: a remote with real content on two branches ----------
rm -rf "$LAB"; mkdir -p "$LAB/src"; cd "$LAB/src" || exit 1
git init -q . && git symbolic-ref HEAD refs/heads/master
mkdir -p model && echo "print('train')" > train.py && echo "cfg" > model/config.yaml
echo "# VeCoR" > README.md
git add -A && $G commit -q -m "initial code"
git checkout -q -b dev_gaze && echo "gaze" > gaze.py && git add -A && $G commit -q -m "gaze work"
git checkout -q -b zh.250101.legacy master
echo "legacy" > legacy.txt && git add -A && $G commit -q -m "legacy work"
git checkout -q master
git clone -q --bare "$LAB/src" "$LAB/remote.git"
URL="$LAB/remote.git"
TODAY6=$(date +%y%m%d)

echo "=== 0. standard library only ==="
# em must stay installable anywhere with no dependency resolution at all.
nonstd=$(python3 - "$EM_SRC/expmonkey/__init__.py" <<'PY'
import ast, sys
mods = set()
for n in ast.walk(ast.parse(open(sys.argv[1]).read())):
    if isinstance(n, ast.Import):
        mods |= {a.name.split(".")[0] for a in n.names}
    elif isinstance(n, ast.ImportFrom) and n.module:
        mods.add(n.module.split(".")[0])
print(" ".join(sorted(m for m in mods if m not in sys.stdlib_module_names)))
PY
)
check "no third-party imports" "$nonstd" ""
grep -q "install_requires" "$EM_SRC/setup.py" && bad "setup.py grew install_requires" \
  || ok "setup.py has no install_requires"

echo "=== 1. em init rejects bad invocations ==="
mkdir -p "$LAB/t1"; cd "$LAB/t1" || exit 1
run_em init >/dev/null 2>&1 && bad "no-arg init should fail" || ok "no-arg init rejected"
mkdir -p "$LAB/t2"; cd "$LAB/t2" || exit 1; touch stray.txt
run_em init "$URL" >/dev/null 2>&1 && bad "non-empty dir should fail" || ok "non-empty dir rejected"
mkdir -p "$LAB/t3"; cd "$LAB/t3" || exit 1; git init -q .
run_em init "$URL" >/dev/null 2>&1 && bad "existing repo should fail" || ok "existing git repo rejected"

echo "=== 2. em init builds an empty project root ==="
V="$LAB/work/VeCoR"; mkdir -p "$V"; cd "$V" || exit 1
run_em init "$URL" >/dev/null 2>&1 || bad "init failed"
[ -d "$V/.em/repo/.git" ] && ok ".em/repo/.git exists" || bad ".em/repo/.git missing"
[ -e "$V/.git" ] && bad "root must not be a worktree" || ok "root has no .git"
check ".em/repo parked on __empty" \
      "$(git -C "$V/.em/repo" rev-parse --abbrev-ref HEAD 2>/dev/null)" "__empty"
[ -f "$V/.em/repo/.git/hooks/pre-commit" ] && ok "pre-commit hook installed" || bad "hook missing"
check "remote-tracking refs fetched" \
      "$(git -C "$V/.em/repo" branch -r --format='%(refname:short)' | grep -v HEAD | tr '\n' ' ' | sed 's/ *$//')" \
      "origin/dev_gaze origin/master origin/zh.250101.legacy"
[ -f "$V/CLAUDE.md" ] && ok "root CLAUDE.md created" || bad "root CLAUDE.md missing"
[ -f "$V/MEMORY.md" ] && ok "root MEMORY.md created" || bad "root MEMORY.md missing"
check "init pins the user into .em/config" \
      "$(grep '^user=' "$V/.em/config" 2>/dev/null)" "user=viktor"

echo "=== 2b. init with an unusable user name: warn, never hang, exit 0 ==="
# stdin is /dev/null so an interactive prompt would EOF rather than block;
# a hang here means the tty check regressed.
mkdir -p "$LAB/t4"; cd "$LAB/t4" || exit 1
out=$(EM_USER=bad.name EM_ALLOW_LOCAL_URL=1 PYTHONPATH="$EM_SRC" \
      python3 -c "import expmonkey; expmonkey.main()" init "$URL" </dev/null 2>&1); rc=$?
check "init still succeeds" "$rc" "0"
echo "$out" | grep -q "em config user" && ok "init points at em config user" || bad "no guidance: $out"
[ -d "$LAB/t4/.em/repo/.git" ] && ok "project still fully built" || bad "project not built"
grep -q '^user=' "$LAB/t4/.em/config" 2>/dev/null && bad "bad name must not be pinned" \
  || ok "bad name not written to config"
# em config rejects at the point of typing, not at the next command
(cd "$LAB/t4" && run_em config user with.dot) >/dev/null 2>&1 \
  && bad "em config accepted a dotted name" || ok "em config rejects a dotted name"
(cd "$LAB/t4" && run_em config user series) >/dev/null 2>&1 \
  && bad "em config accepted the reserved name" || ok "em config rejects 'series'"
(cd "$LAB/t4" && run_em config user viktor) >/dev/null 2>&1
check "em config writes a good name" \
      "$(grep '^user=' "$LAB/t4/.em/config" 2>/dev/null)" "user=viktor"

echo "=== 2c. init installs a shared ignore list for every worktree ==="
EXCL="$V/.em/repo/.git/info/exclude"
[ -f "$EXCL" ] && ok "info/exclude written" || bad "info/exclude missing"
for pat in '__pycache__/' '.idea/' '.DS_Store' '*.png' '*.pdf'; do
  grep -qxF "$pat" "$EXCL" 2>/dev/null && ok "ignores $pat" || bad "missing pattern $pat"
done
# it must actually take effect inside an experiment, not just be a file
cd "$V" || exit 1
run_em cp dev_gaze ignoring >/dev/null 2>&1
IGN="viktor.$TODAY6.ignoring"   # not $G: that is the git alias above
mkdir -p "$V/$IGN/__pycache__" && touch "$V/$IGN/__pycache__/x.pyc"
touch "$V/$IGN/plot.png" "$V/$IGN/keep.py"
check "ignored files stay out of git status" \
      "$(git -C "$V/$IGN" status --porcelain | tr -d ' ' | tr '\n' ' ')" "??keep.py "
check "git add -f still overrides" \
      "$(cd "$V/$IGN" && git add -f plot.png && git status --porcelain plot.png | tr -d ' ')" "Aplot.png"
(cd "$V" && run_em rm "$IGN" -y) >/dev/null 2>&1

echo "=== 2d. re-running never loses hand-written rules ==="
printf 'my-own-secret-dir/\n' >> "$EXCL"
(cd "$V" && run_em hooks install) >/dev/null 2>&1
grep -qxF 'my-own-secret-dir/' "$EXCL" && ok "hand-written rule survived" || bad "hand-written rule lost"
check "em's block is not duplicated" \
      "$(grep -c 'managed by em' "$EXCL")" "2"

echo "=== 3. em cp from a remote branch ==="
cd "$V" || exit 1
MY="viktor.$TODAY6.my_exp"
run_em cp dev_gaze my_exp >/dev/null 2>&1 || bad "em cp failed"
[ -f "$V/$MY/gaze.py" ] && ok "src branch content present" || bad "gaze.py missing"

echo "=== 3b. experiment naming is <user>.<YYMMDD>.<name> ==="
cd "$V" || exit 1
run_em cp dev_gaze named >/dev/null 2>&1 || bad "em cp named failed"
check "dir name" "$(ls "$V" | grep named)" "viktor.$TODAY6.named"
check "branch name" \
      "$(git -C "$V/viktor.$TODAY6.named" rev-parse --abbrev-ref HEAD 2>/dev/null)" \
      "viktor.$TODAY6.named"
(cd "$V" && run_em rm "viktor.$TODAY6.named" -y) >/dev/null 2>&1

echo "=== 3c. reserved / dotted user names are refused ==="
out=$(cd "$V" && EM_USER=viktor.hong EM_ALLOW_LOCAL_URL=1 PYTHONPATH="$EM_SRC" \
      python3 -c "import expmonkey; expmonkey.main()" cp dev_gaze x 2>&1)
echo "$out" | grep -q "em config user" && ok "dotted user refused" || bad "dotted user allowed: $out"
out=$(cd "$V" && EM_USER=series EM_ALLOW_LOCAL_URL=1 PYTHONPATH="$EM_SRC" \
      python3 -c "import expmonkey; expmonkey.main()" cp dev_gaze x 2>&1)
echo "$out" | grep -q "em config user" && ok "reserved user refused" || bad "reserved user allowed: $out"

echo "=== 3d. moving a folder keeps its experiments working ==="
# A plain mv leaves the worktree registered at its old path and marked
# prunable. It still resolves until something prunes -- and em prunes in
# several commands -- so this must run a pruning command (em rm) to be a real
# reproduction rather than a false pass.
cd "$V" || exit 1
mkdir -p "$V/box"
run_em cp dev_gaze moved >/dev/null 2>&1
run_em cp dev_gaze victim >/dev/null 2>&1
MV=$(ls "$V" | grep moved)
VICTIM=$(ls "$V" | grep victim)
mv "$V/$MV" "$V/box/$MV"
echo "dirty" > "$V/box/$MV/uncommitted.txt"
(cd "$V" && run_em rm "$VICTIM" -y) >/dev/null 2>&1   # this prunes
check "moved worktree survives a prune" \
      "$(git -C "$V/box/$MV" rev-parse --abbrev-ref HEAD 2>/dev/null)" "$MV"
[ -f "$V/box/$MV/uncommitted.txt" ] && ok "uncommitted file survived" || bad "uncommitted file lost"
check "registration follows the move" \
      "$(git -C "$V/.em/repo" worktree list --porcelain | grep -c "box/$MV")" "1"
(cd "$V" && run_em rm "box/$MV" -y) >/dev/null 2>&1; rm -rf "$V/box"

echo "=== 3e. series is a plain local folder named series.* ==="
cd "$V" || exit 1
run_em series new tune_lr >/dev/null 2>&1 || bad "series new failed"
[ -d "$V/series.tune_lr" ] && ok "folder series.tune_lr created" || bad "series.tune_lr missing"
[ -f "$V/series.tune_lr/CLAUDE.md" ] && ok "CLAUDE.md copied in" || bad "CLAUDE.md not copied"
[ -f "$V/series.tune_lr/MEMORY.md" ] && ok "MEMORY.md copied in" || bad "MEMORY.md not copied"
[ -e "$V/series.tune_lr/.git" ] && bad "series must not be a worktree" || ok "series is not a worktree"
check "no _series/* branch" \
      "$(git -C "$V/.em/repo" branch --format='%(refname:short)' | grep -c '^_series/')" "0"

echo "=== 3f. em series new normalises an already-prefixed name ==="
run_em series new series.dup >/dev/null 2>&1
[ -d "$V/series.dup" ] && ok "prefix not doubled" || bad "got $(ls -d "$V"/series.series.* 2>/dev/null)"

echo "=== 3g. experiment branch carries no series prefix ==="
cd "$V/series.tune_lr" || exit 1
run_em cp dev_gaze inseries >/dev/null 2>&1 || bad "cp inside series failed"
E="viktor.$TODAY6.inseries"
[ -d "$V/series.tune_lr/$E" ] && ok "experiment nested in series folder" || bad "experiment not nested"
check "branch has no series prefix" \
      "$(git -C "$V/series.tune_lr/$E" rev-parse --abbrev-ref HEAD 2>/dev/null)" "$E"

echo "=== 3h. plain folders are not series ==="
mkdir -p "$V/data"
(cd "$V" && run_em series ls) 2>&1 | grep -q "data" && bad "data listed as series" || ok "data not a series"
rmdir "$V/data"

echo "=== 3i. em series rm removes nested experiments and the folder ==="
(cd "$V" && run_em series rm tune_lr -y) >/dev/null 2>&1
[ -d "$V/series.tune_lr" ] && bad "series folder survived" || ok "series folder removed"
check "nested branch deleted" \
      "$(git -C "$V/.em/repo" branch --format='%(refname:short)' | grep -c "$E")" "0"
(cd "$V" && run_em series rm dup -y) >/dev/null 2>&1

echo "=== 3j. series folders nest to any depth ==="
cd "$V" || exit 1
run_em series new outer >/dev/null 2>&1
cd "$V/series.outer" && run_em series new inner >/dev/null 2>&1
[ -d "$V/series.outer/series.inner" ] && ok "nested series created" || bad "nested series missing"
cd "$V/series.outer/series.inner" || exit 1
run_em cp dev_gaze deep >/dev/null 2>&1 || bad "cp in nested series failed"
D="viktor.$TODAY6.deep"
[ -d "$V/series.outer/series.inner/$D" ] && ok "experiment two levels deep" || bad "not created at depth"
check "deep branch has no folder prefix" \
      "$(git -C "$V/series.outer/series.inner/$D" rev-parse --abbrev-ref HEAD 2>/dev/null)" "$D"
(cd "$V" && run_em ls -a) 2>&1 | grep -q "series.outer/series.inner" \
  && ok "em ls shows the nested path" || bad "em ls missed the nested path"
(cd "$V" && run_em cd "$D") >/dev/null 2>&1 && ok "em cd reaches a deep experiment" || bad "em cd failed at depth"
(cd "$V" && run_em series rm outer -y) >/dev/null 2>&1
[ -d "$V/series.outer" ] && bad "nested series survived rm" || ok "nested series removed"
check "deep branch deleted with it" \
      "$(git -C "$V/.em/repo" branch --format='%(refname:short)' | grep -c "$D")" "0"

echo "=== 3k. em push pushes only the experiment, never a series ==="
cd "$V" || exit 1
run_em series new pushtest >/dev/null 2>&1
cd "$V/series.pushtest" && run_em cp dev_gaze topush >/dev/null 2>&1
P="viktor.$TODAY6.topush"
cd "$V/series.pushtest/$P" || exit 1
echo "result" > out.txt
run_em push -m "test" >/dev/null 2>&1 || bad "em push failed"
check "experiment branch reached the remote" \
      "$(git -C "$LAB/remote.git" branch --format='%(refname:short)' | grep -c "^$P$")" "1"
# Anchored: an experiment may legitimately be named ...inseries, but a series
# itself must never become a branch (it would be _series/<n> or series.<n>).
check "no series branch on the remote" \
      "$(git -C "$LAB/remote.git" branch --format='%(refname:short)' \
         | grep -cE '^_series/|^series\.')" "0"
(cd "$V" && run_em series rm pushtest -y) >/dev/null 2>&1

echo "=== 3l. retired commands are gone ==="
cd "$V" || exit 1
for gone in group mv cm new empty co; do
  run_em "$gone" --help >/dev/null 2>&1 \
    && bad "em $gone should no longer exist" || ok "em $gone retired"
done
run_em hooks install >/dev/null 2>&1 && ok "em hooks install kept" || bad "em hooks install broke"

echo "=== 3m. em cp <branch> adopts it instead of forking ==="
cd "$V" || exit 1
run_em cp dev_gaze adopted >/dev/null 2>&1
A="viktor.$TODAY6.adopted"
cd "$V/$A" && echo "r" > r.txt && run_em push -m "seed" >/dev/null 2>&1
cd "$V" && run_em rm "$A" -y >/dev/null 2>&1     # local gone, remote keeps it
run_em cp "$A" >/dev/null 2>&1 || bad "single-arg em cp failed"
check "adopted dir keeps the original name" "$(ls "$V" | grep adopted)" "$A"
check "adopted branch keeps its identity" \
      "$(git -C "$V/$A" rev-parse --abbrev-ref HEAD 2>/dev/null)" "$A"
check "adopted branch tracks the remote" \
      "$(git -C "$V/$A" rev-parse --abbrev-ref '@{upstream}' 2>/dev/null)" "origin/$A"
[ -f "$V/$A/r.txt" ] && ok "adopted content restored" || bad "content missing"
(cd "$V" && run_em rm "$A" -y) >/dev/null 2>&1

echo "=== 3n. em cp <name> <name> adopts too; a different name still forks ==="
# Uses another user's old-dated branch: forking would restamp it to
# viktor.<today>.legacy, so a passing check here cannot be a same-day coincidence.
cd "$V" || exit 1
L=zh.250101.legacy
run_em cp "$L" "$L" >/dev/null 2>&1 || bad "same-name em cp failed"
check "same-name form keeps user and date" \
      "$(git -C "$V/$L" rev-parse --abbrev-ref HEAD 2>/dev/null)" "$L"
run_em cp "$L" forked >/dev/null 2>&1
check "a different name still forks with the prefix" \
      "$(ls "$V" | grep forked)" "viktor.$TODAY6.forked"
(cd "$V" && run_em rm "$L" -y) >/dev/null 2>&1
(cd "$V" && run_em rm "viktor.$TODAY6.forked" -y) >/dev/null 2>&1

echo "=== 3o. removal is local-only: the remote is never touched ==="
# em rm / em series rm delete a worktree, its dir and its LOCAL branch. The
# remote copy is what makes "clear local space, keep the record" safe, so this
# locks it in: nothing here may ever grow a delete refspec.
cd "$V" || exit 1
run_em cp dev_gaze keeper >/dev/null 2>&1
K="viktor.$TODAY6.keeper"
cd "$V/$K" && run_em push -m "keep" >/dev/null 2>&1
cd "$V" && run_em series new doomed >/dev/null 2>&1
cd "$V/series.doomed" && run_em cp dev_gaze inside >/dev/null 2>&1
I="viktor.$TODAY6.inside"
cd "$V/series.doomed/$I" && run_em push -m "keep" >/dev/null 2>&1
before=$(git -C "$LAB/remote.git" branch --format='%(refname:short)' | sort | tr '\n' ' ')
(cd "$V" && run_em rm "$K" -y) >/dev/null 2>&1
(cd "$V" && run_em series rm doomed -y) >/dev/null 2>&1
after=$(git -C "$LAB/remote.git" branch --format='%(refname:short)' | sort | tr '\n' ' ')
check "remote branch list unchanged by rm" "$after" "$before"
[ -d "$V/$K" ] && bad "local dir survived" || ok "local dir removed"
[ -d "$V/series.doomed" ] && bad "series folder survived" || ok "series folder removed"
check "local branches really gone" \
      "$(git -C "$V/.em/repo" branch --format='%(refname:short)' | grep -c -e "$K" -e "$I")" "0"
# and the removed experiment can be fetched back, which is the point
cd "$V" || exit 1   # series rm just deleted the folder we were standing in
run_em cp "$K" >/dev/null 2>&1
[ -d "$V/$K" ] && ok "removed experiment restorable from the remote" || bad "cannot restore"
(cd "$V" && run_em rm "$K" -y) >/dev/null 2>&1

echo "=== 3p. em rm backs the experiment up before deleting ==="
cd "$V" || exit 1
run_em cp dev_gaze unsaved >/dev/null 2>&1
U="viktor.$TODAY6.unsaved"
cd "$V/$U" && echo "precious result" > result.txt      # never committed, never pushed
cd "$V" && run_em rm "$U" -y >/dev/null 2>&1
[ -d "$V/$U" ] && bad "experiment not removed" || ok "experiment removed"
check "unsaved work was pushed before deleting" \
      "$(git -C "$LAB/remote.git" show "$U:result.txt" 2>/dev/null)" "precious result"

echo "=== 3q. em rm skips the push when already in sync ==="
cd "$V" || exit 1
run_em cp dev_gaze synced >/dev/null 2>&1
S="viktor.$TODAY6.synced"
cd "$V/$S" && echo x > s.txt && run_em push -m "done" >/dev/null 2>&1
head_before=$(git -C "$LAB/remote.git" rev-parse "$S")
cd "$V" && run_em rm "$S" -y >/dev/null 2>&1
check "no extra backup commit was made" \
      "$(git -C "$LAB/remote.git" rev-parse "$S")" "$head_before"
[ -d "$V/$S" ] && bad "experiment not removed" || ok "in-sync experiment removed"

echo "=== 3r. em rm aborts when the backup cannot be pushed ==="
# Someone else pushed to the same branch, so our history is behind: the push is
# rejected and the experiment must survive rather than be silently destroyed.
cd "$V" || exit 1
run_em cp dev_gaze contested >/dev/null 2>&1
C="viktor.$TODAY6.contested"
cd "$V/$C" && echo mine > mine.txt && run_em push -m "mine" >/dev/null 2>&1
(cd "$LAB/src" && git checkout -q -B "$C" && \
 git fetch -q "$LAB/remote.git" "$C" && git reset -q --hard FETCH_HEAD && \
 echo theirs > theirs.txt && git add -A && $G commit -q -m "colleague work" && \
 git push -q "$LAB/remote.git" "$C" && git checkout -q master)
cd "$V/$C" && echo more > more.txt
git -C "$V/$C" fetch -q origin
cd "$V" && out=$(run_em rm "$C" -y 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "rm exits non-zero when backup fails" || bad "rm reported success: $out"
[ -d "$V/$C" ] && ok "contested experiment survived" || bad "experiment was destroyed"
[ -f "$V/$C/more.txt" ] && ok "uncommitted work survived" || bad "uncommitted work lost"
check "colleague's commit still on the remote" \
      "$(git -C "$LAB/remote.git" show "$C:theirs.txt" 2>/dev/null)" "theirs"

echo "=== 3s. em series rm backs its experiments up too ==="
cd "$V" || exit 1
run_em series new backedup >/dev/null 2>&1
cd "$V/series.backedup" && run_em cp dev_gaze nested >/dev/null 2>&1
N="viktor.$TODAY6.nested"
cd "$V/series.backedup/$N" && echo "nested result" > n.txt   # never pushed
cd "$V" && run_em series rm backedup -y >/dev/null 2>&1
[ -d "$V/series.backedup" ] && bad "series not removed" || ok "series removed"
check "nested experiment's work reached the remote" \
      "$(git -C "$LAB/remote.git" show "$N:n.txt" 2>/dev/null)" "nested result"

echo "=== 3t. em push message generation: on by default, pluggable, never blocks ==="
cd "$V" || exit 1
run_em cp dev_gaze aimsg >/dev/null 2>&1
A="viktor.$TODAY6.aimsg"
WIP="wip $(date +%Y%m%d)"
subject() { git -C "$V/$A" log -1 --format=%s; }
cd "$V/$A" || exit 1

# On unless turned off: an unset ai_commit still generates.
echo one > a.txt
EM_COMMIT_MSG_CMD="printf 'lr 1e-4 -> 3e-4, cosine schedule'" \
  run_em push >/dev/null 2>&1
check "generation is on by default" "$(subject)" "lr 1e-4 -> 3e-4, cosine schedule"

(cd "$V" && run_em config ai_commit off) >/dev/null 2>&1
echo two > b.txt
EM_COMMIT_MSG_CMD="printf 'should not run'" run_em push >/dev/null 2>&1
check "ai_commit off disables it" "$(subject)" "$WIP"

(cd "$V" && run_em config ai_commit on) >/dev/null 2>&1
echo two-again > b2.txt
EM_COMMIT_MSG_CMD="printf 'back on'" run_em push >/dev/null 2>&1
check "ai_commit on re-enables it" "$(subject)" "back on"

# The generator sees the staged diff on stdin.
echo three > carrot.txt
EM_COMMIT_MSG_CMD="grep -q carrot.txt && printf 'saw the diff'" \
  run_em push >/dev/null 2>&1
check "the diff is piped to the generator" "$(subject)" "saw the diff"

# A failing generator must never come between you and your remote.
echo four > d.txt
EM_COMMIT_MSG_CMD=false run_em push >/dev/null 2>&1
check "a failing generator falls back to wip" "$(subject)" "$WIP"
check "and the push still happened" \
      "$(git -C "$LAB/remote.git" log -1 --format=%s "$A")" "$WIP"

echo five > e.txt
EM_COMMIT_MSG_CMD=true run_em push >/dev/null 2>&1
check "empty output falls back too" "$(subject)" "$WIP"

# -m means "I wrote it myself" and always wins.
echo six > f.txt
EM_COMMIT_MSG_CMD="printf 'generated'" run_em push -m handwritten >/dev/null 2>&1
check "-m overrides the generator" "$(subject)" "handwritten"

# EM_AI_COMMIT is the documented one-shot escape from "the diff leaves the
# machine", and this switch fails closed: anything plainly negative is off.
echo seven > g.txt
EM_AI_COMMIT=off EM_COMMIT_MSG_CMD="printf 'should not run'" \
  run_em push >/dev/null 2>&1
check "EM_AI_COMMIT=off disables it for one command" "$(subject)" "$WIP"

echo eight > h.txt
EM_AI_COMMIT=0 EM_COMMIT_MSG_CMD="printf 'should not run'" \
  run_em push >/dev/null 2>&1
check "an unrecognised off value still disables it" "$(subject)" "$WIP"

# The generator is a config key, not env-only: pointing at a local model has to
# survive a new shell, or the privacy escape hatch is not one.
(cd "$V" && run_em config commit_msg_cmd "printf 'from config'") >/dev/null 2>&1
check "commit_msg_cmd is persisted" \
      "$(grep -c '^commit_msg_cmd=' "$V/.em/config")" "1"
echo nine > i.txt
EM_COMMIT_MSG_CMD= run_em push >/dev/null 2>&1   # empty: no env override
check "the configured generator is used with no env var set" \
      "$(subject)" "from config"

cd "$V" && run_em rm aimsg -y >/dev/null 2>&1

echo "=== 4. ls / cd / rm find the experiment ==="
cd "$V" || exit 1   # 3g left us inside a series folder that 3i then deleted
run_em ls 2>&1 | grep -q "$MY" && ok "em ls lists it" || bad "em ls missed it"
(cd "$V" && run_em cd "$MY") >/dev/null 2>&1 && ok "em cd works" || bad "em cd failed"
(cd "$V" && run_em rm "$MY" -y) >/dev/null 2>&1 && ok "em rm works" || bad "em rm failed"
[ -d "$V/$MY" ] && bad "dir survived rm" || ok "dir removed"

echo
echo "==================== PASS=$PASS FAIL=$FAIL ===================="
[ "$FAIL" -eq 0 ]
