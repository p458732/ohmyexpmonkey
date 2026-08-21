#compdef em expmonkey
# Fast native zsh completion for em. No Python invocation.

# Enable colored completion listings (needed for the list-colors zstyle below).
zmodload -i zsh/complist 2>/dev/null

# Colors for em candidates, matched directly against the candidate text so
# they work regardless of tag/group context.
#   <series>/<exp>  → series part bold cyan, exp part yellow
#   bare series     → bold cyan
#   flat / bare exp → yellow
# LS_COLORS is inherited first so filename-style coloring still applies to
# anything that doesn't match the em patterns.
zstyle ':completion:*' list-colors \
  "${(s.:.)LS_COLORS}" \
  '=(#b)(series.[^/]##)/(*)=01;36=33' \
  '=series.*=01;36' \
  '=[^/]##.[0-9](#c6).*=33'

# Matcher tiers for em candidates: exact prefix → case-insensitive prefix →
# skip ./_/- separators (e.g. `tune` matches `tune_lr`). True substring
# matching from the middle is handled by the `_em_complete` widget at the
# bottom of this file, because zsh's matcher-list does not reliably match a
# typed substring against the interior of a candidate (especially across `/`).
zstyle ':completion:*:*:(em|expmonkey):*' matcher-list \
  '' \
  'm:{a-zA-Z}={A-Za-z}' \
  'r:|[._-]=* r:|=*'

_em_find_basedir() {
  local p=$PWD
  while [[ "$p" != "/" && -n "$p" ]]; do
    [[ -d "$p/.em" ]] && { print -r -- "$p"; return 0; }
    p=${p:h}
  done
  return 1
}

# Mirror of Python _git_cwd(): the real git dir is basedir, unless the
# .em/repo layout is used (then basedir/.em/repo holds the .git).
_em_git_dir() {
  local basedir=$1
  [[ -d "$basedir/.em/repo/.git" ]] && { print -r -- "$basedir/.em/repo"; return 0; }
  print -r -- "$basedir"
}

# Mirror of Python current_series(): the series whose worktree dir contains PWD,
# or nothing when at basedir root / inside a flat experiment. Used to bias
# completion ordering toward the series the user is currently working in.
_em_current_series() {
  local basedir=$1 rel head
  rel=${PWD#$basedir}
  rel=${rel#/}
  [[ -z "$rel" ]] && return 1
  head=${rel%%/*}
  [[ "$head" == series.* ]] && { print -r -- "$head"; return 0; }
  return 1
}

_em_list_series() {
  local basedir=$1 d n
  setopt local_options null_glob
  for d in "$basedir"/series.*(N/); do
    print -r -- "${d:t}"
  done
  for d in "$basedir"/series.*/series.*(N/); do
    print -r -- "${d:h:t}/${d:t}"
  done
}

# Experiments enumerated from `git worktree list` (naming-independent, so
# classic free-form experiments are included; also works in the .em/repo
# layout). Emits paths relative to basedir: a bare dirname at the root, or
# <series>/<exp> when nested. Series folders have no worktree, so they can
# never show up here; only the .em/repo worktree needs skipping.
_em_list_worktree_exps() {
  local basedir=$1 gd line p rel
  gd=$(_em_git_dir "$basedir")
  git -C "$gd" worktree list --porcelain 2>/dev/null | while IFS= read -r line; do
    [[ "$line" == 'worktree '* ]] || continue
    p=${line#worktree }
    rel=${p#$basedir}
    rel=${rel#/}
    [[ -z "$rel" || "$rel" == .em || "$rel" == .em/* ]] && continue
    print -r -- "$rel"
  done
}

_em_list_branches() {
  local basedir=$1
  git -C "$(_em_git_dir "$basedir")" for-each-ref refs/heads --format='%(refname:short)' 2>/dev/null
}

_em_list_remote_branches() {
  local basedir=$1
  git -C "$(_em_git_dir "$basedir")" for-each-ref refs/remotes --format='%(refname:short)' 2>/dev/null \
    | sed -E 's|^[^/]+/||' | grep -v '^HEAD$' | sort -u
}

# Compadd wrapper that splits candidates into per-kind tags so the list-colors
# zstyles above can color each kind uniformly. Tag name passed by caller is
# only used for the description; actual completion tags are em-series,
# em-series-exp, em-exp.
#
# When _EM_SUBSTR_HINT is set (by the substring-fallback widget below), filter
# candidates to those containing the hint as a substring, so the normal
# completion engine can render the menu with the same colors/sort UX as the
# prefix path. Within each group, reverse-sort so the newest timestamp appears
# first; use compadd -V to preserve that manual order.
_em_compadd() {
  local desc=$1 _ignored_tag=$2
  shift 2
  (( $# )) || return
  local -a inputs
  inputs=( "$@" )
  if [[ -n "$_EM_SUBSTR_HINT" ]]; then
    local hint_lc="${(L)_EM_SUBSTR_HINT}"
    local -a filtered c
    for c in "${inputs[@]}"; do
      [[ "${(L)c}" == *${hint_lc}* ]] && filtered+=( "$c" )
    done
    inputs=( "${filtered[@]}" )
    (( $#inputs )) || return
  fi
  # Context: which series (if any) the user is currently inside. Experiments of
  # that series are split into their own group and floated to the very top.
  local basedir cur
  basedir=$(_em_find_basedir 2>/dev/null)
  [[ -n "$basedir" ]] && cur=$(_em_current_series "$basedir")

  local -a same_series series_exps bare_series flat_exps
  local b
  for b in "${inputs[@]}"; do
    if [[ "$b" == */* ]]; then
      if [[ -n "$cur" && "$b" == "$cur"/* ]]; then
        same_series+=( "$b" )
      else
        series_exps+=( "$b" )
      fi
    elif [[ "$b" == series.* ]]; then
      bare_series+=( "$b" )
    else
      flat_exps+=( "$b" )
    fi
  done
  same_series=( ${(On)same_series} )
  series_exps=( ${(On)series_exps} )
  bare_series=( ${(On)bare_series} )
  flat_exps=(   ${(On)flat_exps} )

  # Group order is the order of these compadd calls. Inside a series, prioritize
  # experiments (same-series first); at basedir root, prioritize series.
  if [[ -n "$cur" ]]; then
    (( $#same_series )) && compadd -V em-same-series -X "experiment (current series)" -- "${same_series[@]}"
    (( $#series_exps )) && compadd -V em-series-exp  -X "$desc" -- "${series_exps[@]}"
    (( $#flat_exps   )) && compadd -V em-exp         -X "$desc" -- "${flat_exps[@]}"
    (( $#bare_series )) && compadd -V em-series      -X "$desc" -- "${bare_series[@]}"
  else
    (( $#bare_series )) && compadd -V em-series      -X "$desc" -- "${bare_series[@]}"
    (( $#series_exps )) && compadd -V em-series-exp  -X "$desc" -- "${series_exps[@]}"
    (( $#flat_exps   )) && compadd -V em-exp         -X "$desc" -- "${flat_exps[@]}"
  fi
}

_em() {
  local -a cmds
  cmds=(
    'init:initialize an em project'
    'series:manage experiment series'
    'cp:fork an experiment, or take a branch as it is'
    'ls:list experiments'
    'cd:cd to experiment or series'
    'rm:remove an experiment'
    'push:commit and push current experiment'
    'hooks:manage git hooks for this em project'
    'config:set local config'
  )

  local curcontext="$curcontext" state line
  typeset -A opt_args
  _arguments -C \
    '1: :->cmd' \
    '*:: :->args'

  case $state in
    cmd)
      _describe -t commands 'em command' cmds
      return
      ;;
  esac

  local basedir
  basedir=$(_em_find_basedir) || return 0

  case $line[1] in
    series)
      if (( CURRENT == 2 )); then
        local -a scmds
        scmds=(
          'new:create a new series folder'
          'ls:list series folders'
          'rm:remove a series folder and its experiments'
        )
        _describe -t series_cmd 'series subcommand' scmds
      elif (( CURRENT == 3 )) && [[ $line[2] == rm ]]; then
        local -a sitems
        sitems=( ${(f)"$(_em_list_series $basedir)"} )
        _em_compadd 'series' series "${sitems[@]}"
      fi
      ;;
    cd)
      local -a items
      items=( ${(f)"$(_em_list_series $basedir)"} ${(f)"$(_em_list_worktree_exps $basedir)"} )
      _em_compadd 'experiment or series' exps "${items[@]}"
      ;;
    rm)
      _arguments \
        '-y[skip confirmation]' \
        '--yes[skip confirmation]' \
        '*:experiment:->exp'
      if [[ $state == exp ]]; then
        local -a items
        items=( ${(f)"$(_em_list_worktree_exps $basedir)"} )
        _em_compadd 'experiment' exps "${items[@]}"
      fi
      ;;
    cp)
      # em cp <src> [<name>]: fork <src> into a new experiment <name>, or with
      # <name> omitted take <src> itself. Both local and remote branches are
      # offered, since the single-arg form is how a remote branch is picked up.
      #   arg 2 (<name>) = free-form, no candidates.
      # Where it lands is decided at runtime by cwd, not here.
      if (( CURRENT == 2 )); then
        local -a brs
        brs=(
          ${(f)"$(_em_list_branches $basedir)"}
          ${(f)"$(_em_list_remote_branches $basedir)"}
        )
        brs=( ${(u)brs} )
        brs=( ${brs:#master} )
        _em_compadd 'source branch' branches "${brs[@]}"
      fi
      ;;
    push)
      _arguments \
        '-m[message]:message:' \
        '--message[message]:message:'
      ;;
    config)
      # Mirrors cmd_config's branches on the Python side; keep in sync.
      if (( CURRENT == 2 )); then
        local -a keys
        keys=('user:set username'
              'ai_commit:generate commit messages from the staged diff'
              'commit_msg_cmd:command that generates them')
        _describe -t keys 'config key' keys
      elif (( CURRENT == 3 )) && [[ $words[2] == ai_commit ]]; then
        local -a vals; vals=('on' 'off')
        _describe -t values 'ai_commit' vals
      fi
      ;;
    hooks)
      if (( CURRENT == 2 )); then
        local -a hcmds; hcmds=('install:install pre-commit large-file check')
        _describe -t hooks_cmd 'hooks subcommand' hcmds
      fi
      ;;
  esac
}

compdef _em em expmonkey

# Substring-fallback completion widget. zsh's matcher-list cannot match a
# typed substring against the middle of a candidate (e.g. typing `debug`
# against `<series>/<user>.<date>.<idx>.debug`); the engine treats `/` as a
# segment boundary and even slash-less interior substrings don't match. The
# widget enumerates candidates itself and rewrites the buffer when only
# substring matches exist. Prefix matches still go through the normal
# completion engine so LCP insertion and menus behave as usual.
typeset -g _EM_TAB_FALLBACK
() {
  local fb="${$(bindkey '^I' 2>/dev/null)##* }"
  if [[ -n "$fb" && "$fb" != "_em_complete" && "$fb" != "undefined-key" ]]; then
    _EM_TAB_FALLBACK="$fb"
  elif [[ -z "$_EM_TAB_FALLBACK" ]]; then
    _EM_TAB_FALLBACK=expand-or-complete
  fi
}

_em_complete() {
  local LBUF=$LBUFFER
  local -a lwords
  lwords=( ${(z)LBUF} )
  local first="${lwords[1]}"

  if [[ "$first" != (em|expmonkey) ]]; then
    zle "$_EM_TAB_FALLBACK"
    return
  fi

  local typed=""
  [[ "$LBUF" != *' ' ]] && typed="${LBUF##* }"

  if [[ -z "$typed" || "$typed" == "$first" ]]; then
    zle "$_EM_TAB_FALLBACK"
    return
  fi

  # Slash in typed text means user is path-completing inside a series; the
  # matcher-list handles per-segment prefix completion well enough there.
  if [[ "$typed" == */* ]]; then
    zle "$_EM_TAB_FALLBACK"
    return
  fi

  local basedir
  basedir=$(_em_find_basedir) || { zle "$_EM_TAB_FALLBACK"; return; }

  local subcmd="${lwords[2]}"
  local -a candidates x
  case "$subcmd" in
    cd)
      candidates=( ${(f)"$(_em_list_series $basedir)"} ${(f)"$(_em_list_worktree_exps $basedir)"} )
      ;;
    rm)
      candidates=( ${(f)"$(_em_list_worktree_exps $basedir)"} )
      ;;
    cp)
      # local + remote: cp forks from either, and its single-arg form takes a
      # remote branch as it is.
      candidates=(
        ${(f)"$(_em_list_branches $basedir)"}
        ${(f)"$(_em_list_remote_branches $basedir)"}
      )
      candidates=( ${(u)candidates} )
      candidates=( ${candidates:#master} )
      ;;
    series)
      # `em series rm <name>`: substring-match series folders.
      case "${lwords[3]}" in
        rm)
          candidates=( ${(f)"$(_em_list_series $basedir)"} )
          ;;
        *)
          zle "$_EM_TAB_FALLBACK"
          return
          ;;
      esac
      ;;
    *)
      zle "$_EM_TAB_FALLBACK"
      return
      ;;
  esac

  local lc="${(L)typed}"
  local -a prefix_m substring_m b
  for b in "${candidates[@]}"; do
    if [[ "${(L)b}" == ${lc}* ]]; then
      prefix_m+=( "$b" )
    elif [[ "${(L)b}" == *${lc}* ]]; then
      substring_m+=( "$b" )
    fi
  done

  # Prefer prefix completion when it can produce matches; the normal engine
  # handles LCP insertion and menus better than we can from a widget.
  if (( $#prefix_m > 0 )) || (( $#substring_m == 0 )); then
    zle "$_EM_TAB_FALLBACK"
    return
  fi

  if (( $#substring_m == 1 )); then
    LBUFFER="${LBUF%$typed}${substring_m[1]}"
    return
  fi

  # Multiple substring hits: strip the typed word and re-enter the normal
  # completion engine with _EM_SUBSTR_HINT set. _em_compadd will filter
  # candidates to substring matches, and the engine handles menu rendering,
  # colors, and LCP insertion just like the prefix path.
  LBUFFER="${LBUF%$typed}"
  typeset -g _EM_SUBSTR_HINT="$typed"
  zle "$_EM_TAB_FALLBACK"
  unset _EM_SUBSTR_HINT
}

zle -N _em_complete
bindkey '^I' _em_complete
