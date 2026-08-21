# expmonkey

基于 `git worktree` 的实验管理工具，面向 AGENT 时代多人 / 多智能体并行开发实验的场景。命令缩写 `em`。

灵感来自 [expmonkey](https://github.com/) 的 `em` 命令。实验是 git 分支 + worktree；系列只是本机上用来分组的文件夹，深度不限，git 完全不知情。

---

## 快速上手（范例）

### 安装

```bash
git clone git@github.com:p458732/ohmyexpmonkey.git
cd ohmyexpmonkey
pip install -e .                             # editable：git pull 后即时生效

# 启用 em shell 函数（自动 cd）+ zsh 原生补全
echo "source $PWD/scripts/em-init.sh" >> ~/.zshrc
source ~/.zshrc
```

> **发行名和 import 名不同**：`pip install` 的包叫 **`ohmyexpmonkey`**，但代码里仍然
> `import expmonkey` —— 这样为经典 expmonkey 写的训练脚本不用改一个字。命令还是 `em`。
>
> 跑训练的 conda 环境若 `import expmonkey`，在每个环境里都 `pip install -e <repo>` 一次（editable，一处 `git pull` 全部更新）。

### 例 1：开一个主题，做一组对比实验

```bash
mkdir myproj && cd myproj
em init git@github.com:you/myproj.git  # 在 .em/repo 建仓库、fetch 远端、装 pre-commit 钩子

em series new tune_lr                  # 建文件夹 series.tune_lr，自动 cd 进去
echo "# lr sweep" >> CLAUDE.md         # 系列里的共享笔记（本地，不进版控）

em cp master baseline                  # 从 master fork 出实验，自动 cd 进 exp 目录
# ... 写代码、训模型 ...
em push -m "first run"                 # = git add -A + commit + push

em cd ..                               # 回系列目录
em cp xy.260620.baseline lr1e-3        # fork 对比实验（建在当前文件夹里）
# ... 改 lr 配置 ...
em push

em ls -a                               # 总览：所有系列文件夹 + 其中的实验 + 根目录的实验
```

### 例 2：把散落在根目录的实验归进 series

不需要专门的命令 —— series 只是文件夹，直接 `mv` 就好。分支名本来就不含系列，
所以移动对 git 和远端毫无影响；worktree 的路径失联由 em 在下一条命令自动修复。

```bash
cd myproj                              # basedir 根
em series new tune_lr                  # 建 series.tune_lr/（若还没有）

mv xy.260620.baseline series.tune_lr/  # 搬一个
mv xy.260620.* series.tune_lr/         # 或用 glob 搬一批

em ls -a                               # 下一条 em 命令自动 git worktree repair
```

### 例 3：接手别人远端的实验，继续对比

```bash
em series ls                           # 看本机有哪些系列文件夹
em cd series.tune_lr                   # 进我关心的系列
em cp zh.260619.adam                   # 单参数 = 原样取回该分支（不 fork）
em cp zh.260619.adam adam_lr1e-4       # 基于它再 fork
```

更细的放置规则、命令清单、Python API 见下文。

---

## 一、核心概念

只有两个对象：**series**（系列）和 **exp**（实验）。

### series（系列）

- **是什么**：一个研究主题、一个对比组、一次 sweep 的容器。本身没有代码，只放共享笔记（`CLAUDE.md`、`MEMORY.md`、画图脚本、汇总表）。
- **命名**：`series.<你起的名字>`，例如 `series.tune_lr`。前缀 `series.` 就是全部的判定依据 —— `em series new tune_lr` 和 `em series new series.tune_lr` 等价，前缀不会重复添加。前缀之后完全自由，没有日期也没有序号。
- **对应的 git 对象**：**没有**。series 就是一个普通文件夹：
  - 没有分支、没有 worktree、没有任何元数据，git 完全不知道它的存在。
  - 可以嵌套：`series.a/series.b/<exp>` 合法，深度不限。
  - 名字不以 `series.` 开头的文件夹（`data/`、`logs/`）永远不会被当成 series。
- **⚠️ series 不同步**：文件夹结构和里面的 `CLAUDE.md` / `MEMORY.md` **只存在于本机**，不进版控、不会被 push、换一台机器不会带过去（那边所有实验会摊平在根目录，且无法重建分组）。这是刻意的取舍：分组是本地的临时组织方式。重要内容请写进实验分支里。
- **可以随便搬**：因为只是文件夹，`mv series.a series.b` 完全合法。git 记录的是绝对路径，搬完里面的实验 worktree 会失联，但 em 在每次 prune 前（以及 `em ls` 时）会自动跑 `git worktree repair` 修回来，未提交的改动也不会丢。

### exp（实验）

- **是什么**：一次具体的训练 / 实验跑。每个 exp 都是一个独立的可工作目录，可以和其它 exp 并行。
- **命名**：`<user>.<YYMMDD>.<你起的名字>`，例如 `xy.260517.baseline`。由文件顶部的全局变量 `EXP_NAME_PREFIX`（默认 `"{user}.{date}."`）决定。
  - **没有序号**。同一个人同一天不要开同名实验，撞了会直接报 `branch already exists`。
  - `{date}` 是六位创建日期 `YYMMDD`（`EXP_DATE_FMT`）。commit 信息里的 `wip <date>` 仍用八位。
  - `{user}` 由 `EM_USER` / `.em/config` / `os.getlogin()` 解析（见末尾"用户名解析"）。**用户名不能含 `.`，也不能叫 `series`** —— 前者会让 `<user>.<date>.<name>` 无法反解析，后者产生的目录名会被误判成 series 文件夹。碰到会直接报错并提示 `em config user <name>`。
  - 想要"打什么就叫什么"，把 `EXP_NAME_PREFIX` 改成空字符串 `""`。
- **对应的 git 对象**：
  - **一个普通 git 分支**：**分支名永远不含所在文件夹**，无论它在根目录还是嵌套在几层 series 里，都叫 `<user>.<YYMMDD>.<name>`。push 上去的就是这个名字。
  - **一个 worktree**：文件夹只决定它放在哪，不影响分支名 —— 所以搬动目录不会让分支失效。
  - 通过 `em push` 提交推送，分支独立演化，互不污染。

### basedir / 隔离

- **basedir** = 含 `.em/` 目录的根，所有 series / exp 都在它下面。`em` 命令通过向上找 `.em/` 来定位 basedir。
- **basedir 本身不是任何分支的 worktree**：真正的仓库在 `.em/repo`（`_git_cwd` 负责解析到它）。basedir 下除了隐藏的 `.em/` 就只有实验 worktree 目录，所以它天生干净，不靠 `.gitignore` 屏蔽任何东西，也不会有哪个分支被 checkout 在那里。
- **每个 worktree 互不可见**：series 只是普通文件夹、不是 worktree，所以实验 worktree 里 `git status` 只看得到自己，永远干净。
- 所有 worktree 共享 `.em/repo/.git/`，`.em/repo/.git/hooks/` 因此对所有 worktree 生效。

### 关系总览

```
basedir/                        ← 不是任何分支的 worktree
├── .em/
│   ├── repo/                   ← 真正的仓库：所有分支、对象、worktree 元数据、hooks
│   └── config                  ← em 配置（em config user 写入）
├── CLAUDE.md                   ← em init 建的空白模板（不进版控）
├── MEMORY.md                   ← 同上；em series new 会把它们拷进新系列
├── series.tune_lr/             ← series：纯本地文件夹，git 不知情
│   ├── CLAUDE.md               ← 从根目录拷来的副本（不进版控）
│   ├── MEMORY.md               ← 同上
│   ├── xy.260517.baseline/     ← exp worktree（分支 xy.260517.baseline，不带 series 前缀）
│   └── xy.260517.lr1e-3/       ← exp worktree（同系列下的对比实验）
│   └── series.gaze/            ← 系列可以嵌套，深度不限
│       └── xy.260518.probe/
├── data/                       ← 名字不以 series. 开头 → 不是系列，em 不管它
└── xy.260517.quick/            ← 根目录下的实验
```

---

## 二、cwd 决定一切

`em cp` 对**当前所在路径**敏感，因为这是判断"新实验应该放进哪个文件夹"的唯一信号。下面这张表是最重要的参考：

| 当前 cwd 所在 | `em cp <src> <n>`（fork）落点 | `em cp <branch>`（原样取回）落点 |
|---|---|---|
| **basedir 根** | 根目录 | 根目录 |
| **系列目录里**（任意 series，含嵌套） | **当前系列下** | **当前系列下** |
| **系列里的某个实验里** | 同一系列下，与该实验平行 | 同一系列下 |
| **根目录下的某个实验里** | basedir 根 | basedir 根 |

**直觉**：「站在哪个文件夹里，新东西就放这个文件夹」。落点**只看 cwd** —— `<src>` / `<branch>` 的名字不影响放哪，因为分支名不带 series 前缀。想放回根目录就先 cd 回 basedir 根。

### `em cp` 放置规则详解（最常用，单独讲清楚）

`em cp <src> <name>` = 基于已有分支 `<src>` fork 出新实验 `<name>`。要分清两个独立的问题：**fork 谁**（由 `<src>` 决定）和 **放哪**（由 cwd 决定）。

#### 1. `<src>` 怎么解析（fork 谁）

按以下顺序匹配：

| 写法 | 含义 |
|---|---|
| `.` | 当前所在实验自己的分支 —— 即"基于我现在这个实验再 fork 一个" |
| 分支名 | `xy.260517.baseline`（分支名不含 series，所以写全名就行） |
| `master` | 本地没有就退到 `main`，再退到 `<remote>/master` / `<remote>/main`（兼容新 clone 和 main 默认分支的仓库） |
| 远端分支名 | 本地都匹配不上时，退到 `<remote>/<src>`。刚 `em init` 完本地只有远端引用，所以可以直接 `em cp <远端分支> <新分支>` |

都匹配不上则报错。补全里 `em cp` 的 src 候选会刻意过滤掉 master（要从 master fork 就自己打 `em cp master <name>`）。

#### 2. 新实验落到哪个文件夹（放哪）

**只看 cwd**：你站在哪个文件夹里，新实验就建在那个文件夹里。`<src>` 的名字完全不影响落点 —— 分支名已经不带 series 前缀，没有"原系列"可以跟随。

一句话记忆：**fork 谁看 `<src>`，放哪看你站在哪。**

例子：

```bash
# 我在系列 A 里，想拿别的实验当起点继续对比 —— 新实验就建在 A 里
cd series.tune_lr
em cp zh.260516.adam adam_lr1e-4
# → 新分支 xy.260518.adam_lr1e-4，目录 series.tune_lr/xy.260518.adam_lr1e-4

# 回到根目录，同样的命令就建在根目录
cd ..
em cp xy.260517.baseline v2
# → 新分支 xy.260518.v2，目录 xy.260518.v2

# 基于自己当前实验再 fork 一个
em cp . retry
```

#### 3. 自动保存 src 的未提交改动

如果 `<src>` 有 checked-out 的 worktree 且里面有未提交改动，`em cp` 会**先**在那个 worktree 里 `git add -A` + commit（消息 `wip <date> (auto by em cp)`），再从这个完整快照 fork。这样你 fork 的是"现在屏幕上看到的样子"，而不是上次 commit 的旧状态。src 干净时不做任何事。

#### 4. 重名怎么办

没有序号，所以同一个人同一天开同名实验会直接报 `branch already exists`。换个名字即可。

### `em cp <branch>` 单参数：原样取回，不 fork

只给一个参数（或第二个参数跟第一个相同）时，`em cp` **不会 fork**：它把该分支
checkout 成 worktree，**保持原本的分支名**并追踪远端，所以可以直接 push 回同一个分支。

```bash
cd series.tune_lr              # 我的系列
em cp zh.260516.cool_idea      # 别人推的实验，原样取回
# → 目录 series.tune_lr/zh.260516.cool_idea，分支仍叫 zh.260516.cool_idea
# → upstream = origin/zh.260516.cool_idea，可直接 em push
```

**这是唯一能接回既有分支的方式。** fork（给不同的 `<name>`）一定会重新盖上
你的用户名和今天的日期：`em cp zh.260516.cool_idea x` 会得到 `xy.260818.x`，
是另一个分支。同理，本地 `em rm` 掉的实验，之后用 `em cp <原分支名>` 取回。

因为分支名不含 series，同一个分支在你这里放哪个文件夹是纯本地的事，不影响远端。

---

## 三、命令一览

| 命令 | 说明 |
|---|---|
| `em init <url>` | 在当前空目录初始化 em 项目：于 `.em/repo` 建仓库、`remote add origin <url>`、`fetch`、装 pre-commit 钩子、写入共享忽略清单。URL 必填（ssh / https），目录必须是空的且不能已是 git repo |
| `em series new <name>` | 新建系列文件夹 `series.<name>`（纯 mkdir + 拷贝根目录的 `CLAUDE.md` / `MEMORY.md` 等）。不建分支、不建 worktree |
| `em series ls` | 列出本机所有系列文件夹（含嵌套） |
| `em series rm <name> [-y]` | 同上，对其下每个实验都先备份再删（任一个备份失败就整个中止）。但文件夹本身和里面的笔记没有版控，删了就没了 |

| `em cp <src> [<name>]` | 给 `<name>`：基于 `<src>` fork 新实验（若 `<src>` 有 worktree 且有未提交改动，会自动 wip commit 后再 fork）。省略 `<name>`（或与 `<src>` 相同）：原样取回 `<src>` 分支，不改名、追踪远端 |
| `em ls [-a]` | 列实验：默认列当前文件夹；`-a` 列全部（所有系列 + 根目录） |
| `em cd <name>` | cd 到指定系列或实验（系列内可省略系列前缀） |
| `em rm <name> [-y]` | 删除实验：**先自动备份**（有未提交改动 / 未推的 commit 就 commit + push），再删 worktree + 目录 + **本地**分支。远端不受影响，之后用 `em cp <分支名>` 就能取回。**备份推不上去（分支分歧 / 远端不可达）会直接中止，不删任何东西** |
| `em push [-m msg]` | `git add -A` + commit + push 当前实验。msg 省略时由 `claude -p` 读 staged diff 生成（见四之四），失败或关闭时退回 `wip <date>` |
| `em hooks install` | （重新）安装 pre-commit 钩子 + 刷新忽略清单。升级 em 后跑一次，老项目就能拿到新规则 |
| `em config user <name>` | 写 `.em/config` 里的 `user=` |
| `em config ai_commit on\|off` | 开关 `em push` 的 commit message 自动生成，默认开启 |
| `em config commit_msg_cmd <cmd>` | 换掉生成命令（默认 `claude -p`），写进 `.em/config` |

### 子命令在 git 层做了什么

| em 命令 | 等价 git 操作（简化） |
|---|---|
| `em init <url>` | `git init .em/repo` + `git remote add origin <url>` + `git symbolic-ref HEAD refs/heads/__empty` + `git commit --allow-empty` + `git fetch origin` + 装 hook |
| `em series new` | `mkdir` + 拷贝根目录文件。**没有任何 git 操作** |
| `em cp <src> <n>` | `git worktree add -b <user>.<YYMMDD>.<n> <当前文件夹>/... <src>`；先在 src worktree 里自动 commit 未保存改动 |
| `em cp <b>` | `git fetch` + `git worktree add [-B <b>] <当前文件夹>/<b>`（保持分支名与 upstream） |
| `em push` | `git add -A` + `git commit` + `git push -u <remote> <branch>` |
| `em rm` | （必要时 `git add -A` + `commit` + `push`）+ `git worktree repair` + `git worktree remove --force` + `git branch -D`（**仅本地**）+ `prune` |

---

## 四、典型工作流

### A. 开新主题做对比实验

```bash
cd myproj                            # basedir
em series new tune_lr                # 建文件夹 series.tune_lr，自动 cd 进去
echo "# tune lr sweep" >> CLAUDE.md  # 系列里的共享笔记（本地，不进版控）
em cp master baseline                # 从 master fork，自动 cd 进 exp
# ...写代码、训模型...
em push -m "first try"               # = git add -A + commit + push

em cd ..                             # 回系列文件夹
em cp xy.260517.baseline lr1e-3      # 基于 baseline fork，建在同一个文件夹里
# 修改 lr 配置...
em push
```

### B. 接手别人推的实验，继续对比

```bash
cd myproj
em series ls                          # 看本机有哪些系列文件夹
em cd series.tune_lr                  # 进我关心的系列
em cp zh.260516.adam_baseline         # 单参数 = 原样取回 → series.tune_lr/zh.260516.adam_baseline
# → 分支名不变，自动 cd 进入新 worktree
# 现在可以基于它再 fork：
em cp zh.260516.adam_baseline adam_lr1e-4
```

### C. 不用系列，全放根目录

```bash
cd myproj                # 在 basedir 根，而不是任何系列文件夹里
em cp master quick_idea  # → xy.260517.quick_idea/（建在根目录）
em cp xy.260517.quick_idea v2   # → xy.260517.v2/（也在根目录）
em ls -a                 # 看全部，包括根目录和系列里的
```

### D. 把散落在根目录的实验归进 series

series 只是文件夹，所以直接 `mv` 就好，没有对应的 em 命令。
分支名本来就不含系列，移动对分支和远端毫无影响 —— 搬错了再搬回来即可。

```bash
cd myproj                          # basedir 根
em series new tune_lr              # 若还没有这个系列

mv xy.260517.baseline series.tune_lr/
mv xy.260517.lr1e-3   series.tune_lr/
# 或一次搬一批：mv xy.260517.* series.tune_lr/

em ls -a                 # 下一条 em 命令会自动 git worktree repair 修好路径
```

---

## 四之二、删除前自动备份

`em rm` / `em series rm` 在真正删东西之前，会检查这个实验有没有远端还没有的内容：

- 工作区有未提交改动或未追踪文件
- 本地有 commit 没推上去
- 这个分支从来没推过

任一成立就**先备份**：`git add -A` + commit（消息 `backup before em rm <date>`）+ push，
然后才删。已经同步的实验不会多产生 commit，也不会多跑一次网络。

```bash
$ em rm viktor.260819.work -y
em: experiment viktor.260819.work: untracked files -- backing up before removal
backed up viktor.260819.work to origin
removed locally: viktor.260819.work
  pushed copies stay on the remote; `em cp viktor.260819.work` brings it back
```

**备份推不上去就中止，什么都不删。** 最常见的原因是分支分歧（别人也推了东西到同一个分支）：

```bash
$ em rm viktor.260819.contested -y
em: could not back experiment viktor.260819.contested up: ... (non-fast-forward)
  the branch has diverged from the remote, or the remote is unreachable.
  Nothing was removed. Resolve the branch (e.g. `git -C <path> pull --rebase`) and try again.
```

> **不会用 `push --force`。** em 本身从不改写历史（没有 rebase / amend / reset），
> 所以本地只会落后、相等或领先远端 —— 领先时普通 push 就能 fast-forward。
> 唯一会分歧的情况是别人推了东西上去，而那正是 `--force` 会删掉别人 commit 的时候。
> 没有跳过备份的开关：安全优先。

---

## 四之三、共享忽略清单

`em init` 会把一份忽略清单写进 **`.em/repo/.git/info/exclude`**，对**所有实验 worktree 一次生效**。

为什么不是项目根目录的 `.gitignore`：根目录不是任何分支的 worktree，放在那里 git 根本看不到。
而 `info/exclude` 在共享的 common dir 里（和 pre-commit 钩子同一个位置），且**不进版控** ——
所以它不会在任何实验里显示成一处改动，也不会污染你 fork 出来的分支。

包含 131 条规则：JetBrains / Python 标准模板 + OS 与编辑器垃圾（`.DS_Store`、`.idea/`、
`.vscode/`、`*.swp`）+ 常见二进制产物（`*.png` `*.jpg` `*.pdf` `*.mp4` 等）。

```bash
# 想提交某个被忽略的文件时，显式加 -f 即可
git add -f figure.png
```

**"以最全面的为主" = 并集，只增不删。** em 管理的规则放在标记块里：

```
# ── managed by em: regenerated by `em hooks install` ──
...131 条规则...
# ── end managed by em ──
你自己加的规则原样保留在这里
```

`em hooks install` 会重写这个块（升级 em 后跑一次就拿到新规则），**块外的内容一个字都不动**。

---

## 四之四、自动生成 commit message（默认开启）

`em push` 不给 `-m` 时，把 staged diff 交给 `claude -p` 生成 commit message：

```bash
# 改了 lr、scheduler、batch size 之后
em push
# → "Switch to cosine schedule, raise lr to 3e-4 and batch size to 64"
```

对照组是从前的 `wip 20260821` —— 三个月后回头看，那行字什么也没告诉你。

**三条规则：**

1. **`-m` 永远优先。** 你自己写了就用你的，不会去问模型。
2. **失败绝不挡 push。** 没装 `claude`、非 0 退出、空输出、超过 30 秒 —— 任何一种都退回
   `wip <date>` 照常推。`em push` 的职责是把工作推上远端，message 只是锦上添花。
3. **不用 conventional commits。** 实验的 commit 不是 `feat:` / `fix:`，要的是「哪个旋钮动了」。

### diff 会离开本机 —— 两个出口

生成靠的是把 staged diff 交给 `claude`，所以你的训练代码会离开这台机器。
仓库涉密、或在不该外传的环境里跑，用下面任一个：

```bash
# 出口一：整个关掉
em config ai_commit off      # 写进 .em/config，退回 wip <date>
EM_AI_COMMIT=off em push     # 只关这一次

# 出口二：换成本机模型，diff 不出网
em config commit_msg_cmd 'ollama run qwen2.5-coder'
```

`ai_commit` 认得 `off` / `false` / `0` / `no`，**认不出的值一律当关闭** ——
这个开关管的是代码出不出网，写错一个字不该让它继续开着。

`commit_msg_cmd` 是**写进 `.em/config` 的**，不是只能靠环境变量：
指向本机模型的设置必须能活过换一个终端，否则这个出口等于没有。
临时改用别的：`EM_COMMIT_MSG_CMD='...' em push`。

### 生成命令拿到什么

em 会把 prompt + `git diff --cached --name-status` + staged patch 从 **stdin** 灌进去，
读 stdout 当 message。所以任何「读 stdin、写 stdout」的命令都能接。

patch 超过 60000 字符就截断（ML 仓库的 diff 很大，尾巴对 message 没有帮助，只会拖慢调用；
em 也不会把整份读进内存 —— 读满就让 git 停下）。

---

## 五、Pre-commit 大文件检查

`em init` 会自动装一个 pre-commit hook 到 `.em/repo/.git/hooks/pre-commit`（路径由 `git rev-parse --git-common-dir` 解析），所有 worktree 共享。

- **行为**：commit 时扫 staged 新增 / 修改的文件，blob 大小用 `git cat-file -s` 算。超过 **60 MiB** 直接 reject，列出每个超标文件大小并给三条出路：
  - `git restore --staged <file>` 撤回 stage
  - 用 git-lfs / 外部存储
  - `git commit --no-verify` 单次绕过
- **已有项目装钩子**：`em hooks install`。如果已存在非 em 管理的 pre-commit，会备份成 `pre-commit.bak`。
- **完全去除**：`rm $(git rev-parse --git-common-dir)/hooks/pre-commit`。

---

## 六、用户名解析

依次尝试：

1. 环境变量 `EM_USER`
2. `.em/config` 里的 `user=<name>`
3. `os.getlogin()`，取不到就退到 `$USER` / `$LOGNAME`

**用户名不能含 `.`，也不能叫 `series`** —— 前者会让 `<user>.<date>.<name>` 无法反解析，
后者产生的目录名会被误判成 series 文件夹。

### `em init` 会把用户名定下来

`em init` 在建好项目后解析一次用户名，并**写进 `.em/config` 钉住**。这样之后换账号
登录、或把目录搬到别的机器，实验命名都不会突然变（`EM_USER` 仍然优先，可临时覆盖）。

解析不出合法名字时（例如公司登录名是 `firstname.lastname` 这种含 `.` 的形式）：

- **交互终端** → 当场问你要用什么名字，输错会重问，直接按 Enter 可略过
  ```
  em: cannot use 'viktor.hong' contains '.', which makes <user>.<date>.<name> impossible to parse back
    user name for this project (letters/digits/_/-, no dots; Enter to skip): viktor
    user:   viktor
  ```
- **非交互**（CI / 脚本 / 管道）→ 不会卡住等输入，只提示下一步：
  ```
    user:   (not set)
    next:   em config user <name>, then em cp <remote-branch> <new-branch>
  ```

两种情况**都不会让 `em init` 失败** —— 项目在这一步之前就已经完全建好了。

`em config user <name>` 随时可以改，**不合法的名字会当场被拒**，不用等到下次建实验。

---

## 七、自动 cd 与 zsh 补全

`em-init.sh`（pip 装好后放在 bin/ 里）提供：

- shell 函数 `em()` 包装 `expmonkey` 二进制：通过 `mktemp` 文件捕获子进程要 cd 的目标目录，子进程退出 0 后父 shell 执行 `cd`。
- zsh 原生补全（`em-completion.zsh`），**零 Python 启动开销**：
  - 系列 / 实验候选 ≈ 60µs/次（纯 zsh glob）
  - 分支候选 ≈ 1.2ms/次（一次 `git for-each-ref`）
  - 支持子串匹配回退：`debug` 能匹配中间是 `debug` 的实验名
- `em cp` 的 src 候选会**过滤掉 master**（要从 master fork 就自己打 `em cp master <name>`）。

加进 `~/.zshrc`：
```bash
source em-init.sh
```

---

## 八、输出着色

TTY 下：

- 系列名：**粗体青色**
- 实验名：**黄色**
- 分支名 `<series>/<exp>` 按 `/` 切分着色

`NO_COLOR=1` 或管道重定向时自动关闭。

---

## 九、Python API（给训练脚本用）

```python
import expmonkey
expmonkey.get_basedir()    # 当前所在 em 项目的 basedir
expmonkey.get_branch()     # 当前 exp 分支名（用作实验 ID）
                          # 优先读 EM_BRANCH 或 EXPMONKEY_BRANCH 环境变量
expmonkey.get_repo_name()  # 仓库名（远端 URL 去掉 .git）；可用 EXPMONKEY_REPO_NAME 覆盖
expmonkey.get_user()       # 解析后的用户名
```

`get_branch()` 在 master / main / `__empty` 上会 raise，确保用作实验 ID 的字符串总是合法 exp 分支名。

### 与经典 expmonkey 的兼容

为了让旧训练脚本（`from expmonkey import get_repo_name, get_branch`）直接照跑，本版本保留了：

- **`get_repo_name()`** —— 经典 API,行为一致（`EXPMONKEY_REPO_NAME` → 远端 URL basename 去 `.git`）。
- **环境变量别名** —— `get_branch()` 同时认 `EXPMONKEY_BRANCH`,`get_repo_name()` 认 `EXPMONKEY_REPO_NAME`。
- **经典自由命名的实验全程可用** —— `em ls` / `em cd` / `em rm` / `em push` 都通过 `git worktree` 识别实验,不依赖 `<user>.<YYMMDD>.<desc>` 命名,所以经典版那种 `hzw.from_lb.dit.baseline`（无日期）的实验也能被列出、进入、提交、删除。`em cp <经典实验> <新名>` 也能用。
- **`.em/repo` 布局** —— Python 端(`_git_cwd`)和 zsh 补全(`_em_git_dir`)都识别 `basedir/.em/repo/.git` 这种经典布局,补全在该布局下同样工作。
- **`em rm` 删搬动过的实验** —— 从 `git worktree` 查出**真实分支名**再删,因此即便目录被 `mv` 过,也不会留下悬空分支。

---

## 十、关于这个版本

这是 **ohmyexpmonkey**（version `0.1.0`）—— [expmonkey](https://github.com/) 的延续与重写：基于 `git worktree` 的全新实现，单文件 `expmonkey/__init__.py`，零第三方依赖，自带原生 zsh 补全。命令缩写沿用 `em`，与经典 expmonkey 的项目布局及 Python API（`get_repo_name` / `get_branch` / `EXPMONKEY_*` 环境变量）保持兼容。仓库保留了 expmonkey 的完整提交历史。