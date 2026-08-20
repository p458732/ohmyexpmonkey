function em() {
    local _out
    _out=$(mktemp)
    _EM_OUTPUT_NEW_PWD=$_out expmonkey "$@"
    local rc=$?
    if [[ $rc -eq 0 ]]; then
        local _new
        _new=$(cat "$_out" 2>/dev/null)
        if [[ -n "$_new" && "$_new" != "$PWD" ]]; then
            cd "$_new" || return $?
        fi
    fi
    rm -f "$_out"
    return $rc
}

export EM_INITED=1

# load completion (zsh native, no Python startup)
if [[ -n "$ZSH_VERSION" ]]; then
    _em_init_dir="${${(%):-%x}:A:h}"
    if [[ -f "$_em_init_dir/em-completion.zsh" ]]; then
        autoload -Uz compinit
        compinit -u -d "${XDG_CACHE_HOME:-$HOME/.cache}/zcompdump-em" 2>/dev/null
        fpath=("$_em_init_dir" $fpath)
        source "$_em_init_dir/em-completion.zsh"
    fi
    unset _em_init_dir
fi
