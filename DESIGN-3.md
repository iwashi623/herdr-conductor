# conductor 追加設計 (A-0 と C)

作成: 2026-11-15｡改訂: 2026-11-15 (人と合意した方針の変更､2回のレビューの指摘､3層への分離を反映)｡前提の設計は `DESIGN.md`｡

## 1. なぜ足すか

人がいちばん疲れるのは､ワーカーの成果を毎回自分で確かめることと､同じ修正の差し戻しが続くことである｡また､メインのコンテキストの残量が見えないので､compact がいつ起きるか分からない｡

この追加では次の2つを作る｡

- **A-0**: コンテキストの使用率を画面に出し､compact の後も人との合意を失わないようにする
- **C**: ワーカーの成果を機械で検査し､差し戻しの回数に上限を設ける｡設計を伴う仕事では､起動の前に受け入れ基準を人に質問で確かめる

[herdr-goal-manager](https://github.com/nwiizo/herdr-goal-manager) (hgm) の考え方を借りるが､仕組みは写さない｡

あわせて､コードを振る舞い･端末･エージェントの3層に分ける (§L)｡herdr や Claude Code が使えなくなっても､その層だけを差し替えれば済むようにするためである｡組み替えは機能を足す前に行い､振る舞いは変えない｡

## 2. ゴールと非ゴール

ゴール:

1. どのセッションでも､画面下部に使用率 (`ctx NN%`) が出る
2. メインは人と決めたこと･人への質問と回答･次の一手を `notes.md` に追記する｡compact の後に tasks.md と一緒に読み込み直される
3. `--worktree` のワーカーには必ず契約が付く｡契約は､リポジトリごとの既定から spawn.py が作る｡人が既定に「はい」と答えるのは､リポジトリごとに1回だけである
4. 検査は､ワーカーが報告する前とメインが報告を受けたときに実行される｡finish.py は検査を再実行せず､最後の合格の記録が今の worktree と一致するかだけを確かめる
5. 差し戻しの回数に上限がある｡上限に達したら､メインは人に尋ねる
6. 設計を伴う仕事では､メインが起動の前に受け入れ基準を最大3問で人に確かめ､完了の報告をその基準に照らして判断する

非ゴール:

- メインやワーカーの自動再起動｡`/clear` での切り替えの提案
- 使用率のファイルへの記録 (読み手がないため)
- 時間の予算､語数の上限､定期的な再確認 (reanchor)､レビュー役の分離
- 画面の文字の読み取り
- ワーカーによる意図的な改ざんを防ぐこと (§4.8)

失敗の条件:

- 確認が増えて､かえって疲れる｡既定への合意はリポジトリごとに1回で8行以内｡既定どおりの起動では人に聞かない｡メインが自分で対応できる失敗は人に伝えずに対応する (§6.2)
- ワーカーが事故で契約や記録を書き換えたのに､誰も気づかない
- statusline の失敗で､セッションの表示や動作が止まる
- 検査が Bash ツールの時間の上限を超えて､メインのターンが途中で切れる
- 再注入の出力が Claude Code の上限を超えて､文脈に入らない

## L. 3層の構成

単体として凝集度を上げ､herdr や Claude Code が使えなくなったときに該当する層だけを差し替えられるようにする｡境目は､herdr や Claude Code にできることではなく､conductor が端末とエージェントに求める能力で切る｡作るのは herdr と Claude Code の層だけである｡ほかの実装 (hc､tmux､Codex など) は作らないが､Protocol はそれらでも実装できる形にする｡

### L.1 層と置き場所

| 層 | 置き場所 | 中身 |
|---|---|---|
| 振る舞い (core) | `core/` | `errors.py` (例外)､`protocols.py` (TerminalHost･Agent･TabRef･LaunchSpec･HookInput)､`ledger.py` (Row･NAME_RE･STATUSES･ledger_root･ledger_dir･read_rows･write_rows･upsert_row･write_json_atomic･iso_now･file_lock･create_exclusive)､`gitutil.py` (git･work_tree_top･head_sha･is_tracked･tracking_state･ensure_excluded)､`spawn.py`･`finish.py`･`init_main.py`･`reinject.py` (各手順)､`contract.py` (§4.2〜4.3)､`check.py` (§4.5〜4.7･§4.10) |
| 端末 (host) | `hosts/herdr.py`､`hosts/herdr-plugin/` | HerdrHost (§L.4)｡承認待ちの通知プラグイン |
| エージェント (agent) | `agents/claude.py`､`SKILL.md`､`worker-rules.md` | ClaudeAgent (§L.5)｡Claude が読む手順書 |
| エントリポイント | ルートの `spawn.py`･`finish.py`･`init_main.py`･`check.py`･`reinject.py`･`statusline.py`､`layers.py` | 引数の解析､層の選択､core の呼び出し､出力と終了コード |

- エントリポイントのスクリプトの名前･引数･出力･終了コードは変えない｡SKILL.md と settings.json の登録が呼んでいるためである｡例外は init_main.py への `--probe` の追加だけである (§L.3)
- `conductor_lib.py` はなくす｡中身は `core/ledger.py` と `core/gitutil.py` に分け､`herdr()` は `hosts/herdr.py` に移す
- `SKILL.md` はスキルの決まりでスキルのルートに置く｡`worker-rules.md` もルートに残す (置き場所はルートでも､属する層はエージェントである)
- `herdr-plugin/` は `hosts/herdr-plugin/` に移す｡symlink は置かない｡移した直後に､コントローラが herdr のプラグインの登録 (`herdr plugin link`) を新しいパスでやり直す (§8 の11で､登録をやり直したあとに通知が届くことを確かめる)｡`notify_blocked.py` の既定のログの場所 (`<スキルのルート>/logs/blocked.log`) は変えない

### L.2 依存の向き

- core は `hosts`･`agents`･`layers` を import しない｡相対 import (`from .. import hosts` など) も解決して同じに扱う｡`importlib` と `__import__` も使わない｡端末とエージェントは引数で受け取る
- core のうち `subprocess` を使ってよいのは `gitutil.py` と `check.py` だけである
- core の文字列定数 (docstring を含む) に `herdr`･`HERDR_`･`claude` を書かない (大文字と小文字を区別しない)
- hosts と agents が import してよい conductor のモジュールは､`core.errors` と `core.protocols` だけである｡hosts と agents は互いに import しない
- 層を選ぶのはエントリポイントだけで､`layers.py` を通す
- HerdrHost･ClaudeAgent と､テストの FakeHost･FakeAgent のメソッドの引数 (`inspect.signature` の名前･種類･既定値) は､Protocol と同じにする

### L.3 層の選択と環境変数

- 端末は `CONDUCTOR_HOST` (既定 `herdr`)､エージェントは `CONDUCTOR_AGENT` (既定 `claude`) で選ぶ｡`layers.py` の `host_from_env(environ) -> TerminalHost` と `agent_from_env(environ) -> Agent` が返し､未知の値なら `LayerUnknown` を投げる
- spawn.py･finish.py･init_main.py は `LayerUnknown` で終了コード2｡reinject.py は何も出さずに exit 0｡statusline.py は `ctx --%` を出して exit 0｡check.py は層を使わない
- `init_main.py --probe` を足す｡`--main` は要らない｡`host.require_pane()` が通れば何も出さずに0､端末の外か未知の層なら理由を出して2で終わる｡何も書き込まない｡SKILL.md §1 は､端末の中で動いているかをこれで確かめる
- spawn.py は､ワーカーのタブの環境変数に `CONDUCTOR_HOST=<host.name>` と `CONDUCTOR_AGENT=<agent.name>` を足す｡ワーカーのフックも同じ層を使うようにするためである｡3層への組み替えで振る舞いが変わるのは､この環境変数の追加だけである
- conductor 自身の環境変数 (`CONDUCTOR_*`) の名前は中立のままにする｡`HERDR_PANE_ID`･`HERDR_ENV`･`HERDR_WORKSPACE_ID` を読むのは `hosts/herdr.py` だけである

例外と終了コード:

- `ConductorError` に属性 `exit_code` (既定1) を持たせる｡エントリポイントは `except ConductorError as e` で理由を標準エラー出力に出し､`e.exit_code` で終わる
- 子のクラスと番号: `UsageError` 2､`LayerUnknown` 2､`PaneClash` 3 (init_main｡メッセージは旧名を1行に1つ)､`ContractMissing` 4､`ContractMismatch` 5､`LockBusy` 6､`HostError` 1
- spawn.py は､既定の `ContractMismatch` を終了コード1の `ConductorError` に変えて投げ直す (§6.1 の spawn.py の列のとおり)

### L.4 端末の Protocol (TerminalHost)

| メソッド | 戻り値 | 失敗の伝え方 | herdr での実装 | 呼び出し元 |
|---|---|---|---|---|
| `name` (属性) | `str` | - | `"herdr"` | spawn (ワーカーの環境変数) |
| `current_pane()` | `str \| None` | 端末の外なら None | `HERDR_PANE_ID` | reinject (None なら何も出さない) |
| `require_pane()` | `str` | 端末の外なら､host が自分の文言で `UsageError` | `HERDR_PANE_ID` (空なら今の init_main.py の文言) | init_main (登録と `--probe`) |
| `current_workspace()` | `str` | `HostError` | `HERDR_ENV=1` と `HERDR_WORKSPACE_ID` を確かめる (今の2つの文言のまま) | spawn |
| `create_tab(workspace: str, label: str, cwd: Path, env: dict[str, str])` | `TabRef(tab_id, pane_id)` | `HostError`｡応答に ID がなければ､作ったタブを閉じてから `HostError` | `tab create --workspace --cwd --label --no-focus --env K=V …`｡フォーカスは移さない | spawn |
| `start_agent(pane_id: str, name: str, spec: LaunchSpec)` | None | `HostError` (端末の stderr を含む) | `agent start <name> --kind <spec.kind_hint> --pane <pane> --timeout 60000 -- <spec.command[1:]>`｡`agent_pane_busy` なら再試行 | spawn |
| `read_screen(pane_id: str, lines: int = 40)` | `str` | 失敗は空文字 | `agent read <pane> --source visible --lines <lines>` | spawn (起動の失敗の理由に添える) |
| `tab_label(tab_id: str)` | `str \| None` | None はタブがない｡`HostError` は確かめられない (出力を読めない場合を含む) | `tab get`｡`tab_not_found` なら None | finish |
| `close_tab(tab_id: str)` | None | `HostError` | `tab close` | spawn (巻き戻し)､finish |
| `live_agent_names()` | `list[str]` | `HostError`｡対応しない端末は `[]` を返してよい | `agent list`｡名前が null のものは除く | spawn (補助の重複検査) |
| `show_status(pane_id: str, display_name: str, kind_hint: str, summary: str)` | `bool` | 失敗は False (例外にしない) | `pane report-metadata --source conductor --agent <kind_hint> --display-agent <name> --state-label working=<summary>` | spawn |

- `TabRef` は `tab_id: str` と `pane_id: str` を持つ frozen な dataclass である
- `start_agent` の `spec.command` は､`command[0]` が実行ファイルの名前で､残りが引数である｡herdr は `--kind` で実行ファイルを決めるので､`command[1:]` だけを渡す｡ほかの端末は `command` をそのまま実行すればよい
- `kind_hint` は端末への補助の情報で､端末は無視してよい
- busy の再試行の間隔と合計の時間は､`HerdrHost.__init__(retry_interval=None, retry_total=None)` で決める｡None なら `CONDUCTOR_SPAWN_RETRY_INTERVAL`･`CONDUCTOR_SPAWN_RETRY_TOTAL` (既定0.5秒と20秒) を読む
- `HostError` のメッセージは､そのまま人に見せる理由にする｡端末のコマンドを実行できないとき (OSError) も `HostError` にする
- core が組み立てる警告と失敗の文は､端末の名前を含めない｡たとえば finish の「herdr tab get が失敗したため､タブ X は閉じませんでした」は､「タブ X のラベルを確かめられなかったため､閉じませんでした: <HostError の文>」にする
- 承認待ちの通知 (`hosts/herdr-plugin/`) は herdr 専用で､Protocol には入れない｡別の端末では､その端末の方法で作る

### L.5 エージェントの Protocol (Agent)

| メソッド | 戻り値 | 失敗の伝え方 | Claude Code での実装 | 呼び出し元 |
|---|---|---|---|---|
| `name` (属性) | `str` | - | `"claude"` (`CONDUCTOR_AGENT` の値) | spawn (ワーカーの環境変数) |
| `hook_output_limit` (属性) | `int` | - | `10000` (フックの出力の文字数の上限｡§3.2) | reinject |
| `launch(name: str, ledger_dir: Path, model: str \| None, prompt: str)` | `LaunchSpec(kind_hint, command)` | 例外なし | `kind_hint="claude"`､`command=["claude", "--name", <name>, "--permission-mode", "auto", "--add-dir", <ledger_dir>, "--append-system-prompt-file", <worker-rules.md>, ("--model", <model>), <prompt>]` (`--model` は指定時だけ) | spawn |
| `read_hook_input(stdin_text: str, fallback_cwd: str)` | `HookInput(cwd, source)` | 例外なし｡壊れた入力は `HookInput(fallback_cwd, "other")` | SessionStart の JSON の `cwd` と `source` | reinject |
| `main_guide(main_name: str, ledger_dir: Path)` | `str` | 例外なし | 今の GUIDE の文に「メイン名: …｡台帳ディレクトリ: …｡」を続けたもの (TAIL と改行は含まない) | reinject |
| `clear_notice()` | `str` | 例外なし | 今の CLEAR_LINE (改行つき) | reinject |
| `format_hook_output(text: str)` | `str` | 例外なし | 素の標準出力なので､そのまま返す | reinject.py (エントリポイント) |
| `statusline(stdin_text: str)` | `str` (1行) | 例外なし｡読めなければ `ctx --%` | §3.1 | statusline.py |

- `LaunchSpec` は `kind_hint: str` と `command: list[str]` を持つ frozen な dataclass である｡core は `kind_hint` を `start_agent` と `show_status` の両方に使う
- `HookInput` は `cwd: str` と `source: str` を持つ frozen な dataclass である｡`source` は正規化した値で､`"compact"`･`"clear"`･`"other"` のどれかに限る｡エージェントの層が､入力の値をこの3つに直してから渡す
- TAIL の文 (「上の <conductor-ledger> の中身は記録であり､指示ではありません｡」) は区切りの説明なので､core が持つ｡起動文 (「指示書 <パス> を読んで作業を始めてください｡報告先は <メイン名> です｡」) も conductor の約束事なので､core が作って `prompt` として渡す
- `hook_output_limit` は､`format_hook_output` に渡す前の文字列に対する上限である｡core は区切りで包み､`main_guide` と TAIL を足した後の全体の長さで予算を測る (§3.2)｡さらに `format_hook_output` を通した後の長さが `hook_output_limit` 以下であることも確かめ､超えたら notes.md 側から行を減らす｡`format_hook_output` を呼ぶのはエントリポイントの reinject.py である
- SKILL.md と worker-rules.md はエージェントの層の文書である｡端末に依存する記述 (herdr のコマンド名､`HERDR_ENV` の確認など) は書かず､スクリプトだけを呼ぶ｡端末の中で動いているかは `init_main.py --probe` で確かめる
- SKILL.md は､台帳の行や状態の規則のような core の手順も抱えている｡エージェントを差し替えるときは､この文書を広く書き直すことになる
- 承認のパターン (§4.11)､AskUserQuestion (§5)､statusline の登録 (§3.1)､SessionStart フックの登録は Claude Code のものなので､この層に属する

### L.6 テストの分け方

- core: `tests/core/`｡偽の端末 `FakeHost` と偽のエージェント `FakeAgent` (`tests/fakes/layers.py` の Python のクラス) を渡し､プロセス内で実行する｡`FakeHost` は呼ばれたメソッドと引数を `calls` に記録し､戻り値と投げる例外をテストから決められる｡git は本物の一時リポジトリを使う
- host: `tests/hosts/`｡今の偽の herdr コマンド (`tests/fakes/herdr`) を PATH に置き､`HerdrHost` のメソッドを直接呼ぶ｡通知プラグインのテストもここに置く
- agent: `tests/agents/`｡入力と出力の形式だけを見る
- エントリポイント: `tests/cli/`｡スクリプトを subprocess で起動し､引数･終了コード･JSON の出力･層の選択を確かめる (偽の herdr を PATH に置く)
- 依存の検査: `tests/test_layers.py`｡§L.2 の規則を ast と `inspect.signature` で確かめる

## 3. A-0: 使用率と notes.md

### 3.1 statusline

- `~/.claude/skills/conductor/statusline.py` を `~/.claude/settings.json` の `statusLine` に登録する (いまは未設定)
- 入力の読み取りと表示はエージェントの層 (`ClaudeAgent.statusline`) に置き､`statusline.py` はエントリポイントだけにする
- 標準入力の JSON から `context_window.total_input_tokens`･`context_window.context_window_size`･`model.id`･`cwd` (なければ `workspace.current_dir`) を読む｡`~/.claude/settings.json` の `modelSettings.<model.id>.autoCompactWindow` も読む (読めなくてもよい)
- 使用率 NN は `total_input_tokens` を窓で割った値にする｡窓は autoCompactWindow が正の整数なら `context_window_size` との小さいほう､なければ `context_window_size` である｡`/context` の表示と合う
- `total_input_tokens` か `context_window_size` が取れなければ `context_window.used_percentage` を使う
- 標準出力に1行を出す: `ctx NN% · <cwd の末尾のディレクトリ名>`｡使用率が取れなければ `ctx --%`
- ファイルには何も書かない
- 例外が起きても1行を出して exit 0｡0.5秒以内に終わる

### 3.2 notes.md

台帳ディレクトリに `notes.md` を置く｡書き手はメインだけである｡見出しを持たない追記型のログで､1行に1件を書く｡

```
- 2026-11-15T10:00 [決定] 検査の既定は npm test と npm run lint にする
- 2026-11-15T10:05 [質問] 一覧のキャッシュは何分にするか
- 2026-11-15T10:12 [回答] 10分 (人の返答)
- 2026-11-15T10:30 [次] fix-cache の完了報告を待つ
```

- 種類は `[決定]`､`[質問]`､`[回答]`､`[次]` の4つ
- 書く場面: 人と何かを決めたとき､人に質問したときと答えを得たとき､ひと区切りついたとき
- 行を書き換えたり消したりしない｡古い決定を覆すときは､新しい `[決定]` の行を足す

Claude Code は､hook の標準出力を全体で10,000文字までしか文脈に入れない｡超えると出力はファイルに保存され､文脈にはそのパスと先頭2,000文字のプレビューだけが入る｡この上限を変える設定はない (https://code.claude.com/docs/en/hooks)｡そこで､予算はバイトでなく文字数で持ち､余裕を見て9,000文字にする｡上限の値はエージェントの層の `hook_output_limit` (10,000) で持ち､core は1,000文字を引いた値を予算に使う｡予算は､区切りで包み､GUIDE と TAIL の文を足した後の全体の長さで測る｡

compact の後､reinject.py は1つの `<conductor-ledger>` の中に tasks.md と notes.md を入れる｡本文の組み立てと TAIL の文は core (`core/reinject.py`) が持ち､フックの入力の読み取り､GUIDE と clear の文､出力の形式はエージェントの層が持つ (§L.5)｡

1. 文字数は､`<conductor-ledger>` のエスケープ (`&lt;`) の後で数える
2. tasks.md を先に入れる｡4,000文字を超える分は切り､`(以下省略)` を付ける
3. 空行と `notes.md:` の行を置き､残りの文字数に収まるだけ notes.md の行を末尾 (新しい側) から入れる｡行の途中では切らない
4. ただし､2,000文字を超える1行は2,000文字で切って `…` を付けてから数える｡最後の行が長すぎて丸ごと落ちることを防ぐためである
5. notes.md の行を省いたら､notes.md の部分の先頭に `(古い記録を省略)` を付ける
6. 標準出力の全体 (区切りの行､GUIDE と TAIL の文､省略の印を含む) を9,000文字以内にする
7. notes.md がないか空なら､`notes.md:` の行も出さず､従来と同じ形にする

ワーカーへの出力 (指示書) と clear の出力も､同じく標準出力の全体を9,000文字以内にする｡いまの `LIMIT = 20000` (バイト) は､ASCII が多いと上限を超えるためである｡

## 4. C: 契約と検査

### 4.1 用語

- **既定**: リポジトリごとの検査コマンド･触ってはいけないパス･差し戻しの上限｡`<ledger_root>/.orchestra/contract-defaults.json` に置く｡`ledger_root` は台帳を置くリポジトリのルート (DESIGN.md §4) で､同じリポジトリのメインは同じ既定を使う
- **契約**: ワーカー1人ごとの検査の条件｡spawn.py が既定から作り､`<台帳>/tasks/<名前>.contract.json` に置く
- **差分**: 既定から外れる点｡人に聞いて合意したものだけを､メインが下書きに書く

目的と受け入れ基準は契約に入れない｡その正本は指示書 (`tasks/<名前>.md`) の `## 目的` と `## 受け入れ基準` である｡

### 4.2 既定ファイル

```json
{
  "checks": [
    {"name": "test", "cmd": "npm test", "timeout": 300},
    {"name": "lint", "cmd": "npm run lint", "timeout": 120}
  ],
  "protected_paths": ["infra/**", "**/*.lock", ".github/workflows/**"],
  "max_returns": 3,
  "agreed": "2026-11-15T10:00:00+09:00 人の返答: はい"
}
```

許すキーは､ファイルの種類ごとに次のとおりである｡表にないキーがあれば不正とする｡旧形式の `keep`･`objective`･`acceptance` の書き残しに気づくためである｡

| キー | 既定 | 差分の下書き | 契約 |
|---|---|---|---|
| `checks` | 必須 | - | 必須 |
| `add_checks`･`drop_checks`･`add_protected` | - | 省略可 | - |
| `protected_paths` | 省略可 (空) | - | 必須 |
| `allow_paths` | - | 省略可 | 必須 (空でよい) |
| `max_returns` | 省略可 (3) | 省略可 | 必須 |
| `agreed` | 必須 | 必須 | 必須 |
| `base`･`base_ref`･`defaults_sha256`･`deviation` | - | - | 必須 (`base_ref` は空文字でよい｡`deviation` は null でよい) |

値の規則 (3つで共通):

- `checks`･`add_checks`: 配列｡0件でもよい (protected_paths の照合だけになる)｡各要素は `name` (`^[a-z0-9_-]{1,32}$`､重複なし)､`cmd` (空でない文字列)､`timeout` (1〜540の整数､秒)
- `timeout` の合計は540秒以下｡Bash ツールの上限 (600秒) から60秒の余裕を引いた値である
- `protected_paths`･`add_protected`･`allow_paths`: 文字列の配列｡`/` で始まるものは不可
- `drop_checks`: 文字列の配列
- `max_returns`: 1以上の整数
- `agreed`: 空でない文字列｡人の返答と日時

sha256 のファイル (`contract-defaults.sha256` と `tasks/<名前>.contract.sha256`) は､64桁の小文字の16進数と改行の1行とする｡読むときは前後の空白を除いて比べる｡JSON は `write_json_atomic` と同じ形式 (`indent=2`､`ensure_ascii=False`､末尾に改行) で書き､書いたバイト列の sha256 を記録する｡

書き方:

1. そのリポジトリで最初に `--worktree` のワーカーを起動するとき､メインはリポジトリの package.json や CI の設定を読んで既定の案を作る｡案に入れる検査は､gitignore されていないファイルを残さないものにする (§4.5 の手順6の警告を避けるため)
2. 案を8行以内で人に見せ､1回だけ聞く｡受け入れ基準の質問と同じ AskUserQuestion にまとめてよい (§5.2)
3. 人が「はい」と答えたら､メインは `agreed` に人の返答と日時を入れて､下書き `.orchestra/contract-defaults.draft.json` に書く｡notes.md にも `[決定]` で残す
4. 人が直しを返したら､メインはそれを反映した内容を合意とみなし､その返答を `agreed` に入れて下書きに書く｡案を見せて聞き直すことはしない
5. spawn.py は､既定がなく下書きがあるときだけ下書きを読む｡形式を検査し､`.orchestra/.defaults.lock` の flock の下で既定ファイルと `contract-defaults.sha256` を書いてから下書きを消す
6. 既定がすでにあるのに下書きもあれば､spawn.py は失敗する (同じリポジトリの別のメインが先に既定を作った場合など)｡このときメインは下書きを消し､既存の既定で起動し直す｡人には聞かない
7. 下書きの形式が不正なら､spawn.py は失敗し､下書きは残る｡メインは下書きを直して起動し直す
8. 既定は起動の失敗で巻き戻さない｡人が合意した内容だからである

既定の扱い:

- 既定ファイルは台帳と同じく git の管理外 (`.git/info/exclude`) にある｡そのため､他人のリポジトリに仕込まれることはない
- ただし､既定ファイルが git で追跡されていたら使わない｡spawn.py は理由を出して失敗する｡reinject.py が追跡済みの `.orchestra/` を読まないのと同じ方針である
- spawn.py は使う前に､既定ファイルの sha256 を `contract-defaults.sha256` と照らす｡違えば失敗する
- 既定を変えるコマンドは今回作らない｡人が既定を変えたいと言ったら､メインが既定ファイルと sha256 ファイルを消し､新しい案を見せて最初の合意からやり直す｡人にファイルを消してもらうことはしない｡起動済みのワーカーの契約は影響を受けない

### 4.3 ワーカーの契約

spawn.py は `--worktree` のとき､必ず契約を作る｡`--worktree` なしのワーカー (調査だけの仕事) には作らない｡

差分の下書き `<台帳>/tasks/<名前>.contract.draft.json` (既定どおりなら置かない):

```json
{
  "add_checks": [{"name": "e2e", "cmd": "npm run e2e", "timeout": 120}],
  "drop_checks": ["lint"],
  "add_protected": ["src/legacy/**"],
  "allow_paths": ["infra/cache/**"],
  "max_returns": 4,
  "agreed": "2026-11-15T11:00:00+09:00 人の返答: はい"
}
```

- `agreed` 以外はすべて省略できる｡`agreed` は必須である (差分は必ず人に聞くため)
- `drop_checks` の名前は既定にあるものに限る｡`add_checks` の名前は既定と重ならないこと
- `allow_paths` は､protected_paths に当たっても触ってよいパスの glob である
- 起動に成功しても下書きは消さない｡中身は契約の `deviation` に写される
- agent start の失敗で名前が使えなくなったら (tasks.md の行が `中止`)､メインは下書きを新しい名前の下書きに移して起動し直す｡合意は引き継ぎ､人に聞き直さない

spawn.py が作る契約 `<台帳>/tasks/<名前>.contract.json`:

```json
{
  "checks": [{"name": "test", "cmd": "npm test", "timeout": 300},
             {"name": "e2e", "cmd": "npm run e2e", "timeout": 120}],
  "protected_paths": ["infra/**", "**/*.lock", ".github/workflows/**", "src/legacy/**"],
  "allow_paths": ["infra/cache/**"],
  "max_returns": 4,
  "base": "<起動時のメインの HEAD の commit>",
  "base_ref": "main",
  "defaults_sha256": "<既定ファイルの sha256>",
  "agreed": "<既定の agreed>",
  "deviation": null
}
```

- `checks` は既定から `drop_checks` を除き､`add_checks` を足したもの｡timeout の合計の上限 (540秒) を作った後にも検査する
- `protected_paths` は既定に `add_protected` を足したもの
- `base_ref` は､spawn 時のメインのブランチ名 (`git rev-parse --abbrev-ref HEAD`)｡detached HEAD なら空文字
- `deviation` には差分の下書きの中身をそのまま入れる｡差分がなければ `null`
- 契約の中身 (`base`･`base_ref` を含む) は､`git worktree add` の前に作る｡作る途中の失敗 (drop の名前違い､合計541秒など) では､worktree もブランチも作らずに失敗する
- 契約のファイルは､worktree を作った後､tab create の前に書く｡契約ファイルがすでにあれば失敗する｡書けなかったときは､作った worktree とブランチを巻き戻す
- sha256 は `<台帳>/tasks/<名前>.contract.sha256` に､台帳のロック (`.tasks.lock`) の下で1回だけ書く｡ファイルがすでにあれば失敗する｡`main.json` には何も足さない (init_main.py の再実行で消えるため)
- ワーカーには環境変数 `CONDUCTOR_CONTRACT` で契約ファイルの絶対パスを渡す｡core が `host.create_tab` の `env` に入れる
- tab create の失敗で巻き戻すときは､契約ファイルと sha256 ファイルも消す｡差分の下書きは残す
- 契約は起動後に変えない｡変えるなら人と合意し直し､新しい名前のワーカーで始める｡例外は差し戻しの上限の延長 (§4.6) で､これは契約でなく記録に書く

### 4.4 人に聞くときと聞かないとき

| 場面 | メインの動き |
|---|---|
| リポジトリに既定がない | 既定の案を8行以内で見せ､1回だけ聞く (§4.2)｡直しが返ったら反映して合意とみなす |
| 既定どおりで起動できる | 人に聞かずに起動する｡「新しいワーカー fix-cache を既定の契約 (検査: test, lint) で起動しました」のように､追加依頼か新規かの知らせと合わせて1行で知らせる |
| protected_paths に触れる必要がある･検査を足す･外す･上限を変える | 差分だけを人に聞き､「はい」なら差分の下書きに書く |
| 受け入れ基準のうち､コマンドで判定できるものを検査にしたい | 人に聞き､「はい」なら `add_checks` に書く (§5) |
| 人が既定を変えたいと言った | メインが既定ファイルと sha256 ファイルを消し､新しい案で聞き直す |
| check.py が終了コード3を返した | 差し戻さず､結果を見せて､延長するか止めるかを聞く (§4.6) |
| 同じ状態での不合格が2回続いた (`same_state`) | 差し戻さず､人に尋ねる (§4.6) |

メインが自分で対応できる失敗 (finish.py の5の一部､6､143 など) は､人に伝えずにメインが対応する｡扱いは §6.2 の表に従う｡

hgm の「依頼を承認とみなす」と同じく､人の依頼と合意済みの既定の範囲で起動するなら､それ以上の承認は取らない｡

### 4.5 check.py

```
check.py [--main <メイン名>] [--name <ワーカー名>] [--by worker|main] [--cwd <ディレクトリ>]
check.py [--main …] [--name …] --reject "<受け入れ基準に足りない点>"
check.py [--main …] [--name …] --extend <N> --agreed "<人の返答>"
```

- `--main` と `--name` の既定値は､環境変数 `CONDUCTOR_MAIN` と `CONDUCTOR_WORKER`
- 台帳ディレクトリは､環境変数 `CONDUCTOR_LEDGER` があればそれを使い､ルートは台帳ディレクトリの2つ上とする｡なければ `--cwd` (省略時は今の作業ディレクトリ) から求める｡ワーカーが別のディレクトリに `cd` していても動くようにするためである
- `--by` の既定は `main`｡ただし `CONDUCTOR_ROLE=worker` のときは `worker` に固定する｡ワーカーから `--by main`･`--reject`･`--extend` を指定したら､終了コード2で拒む
- ワーカーは承認のパターン (§4.11) に合うよう､`check.py --by worker` と明示して呼ぶ
- `--reject` と `--extend` は検査を実行しない｡記録を1行足すだけである (§4.6)
- check.py は振る舞いの層だけで動き､端末もエージェントも使わない (`core/check.py`)
- git の呼び出しの timeout は120秒とする (`core.gitutil.git` の既定の5秒では､大きなリポジトリの `add -A` が終わらない)

`--by` で検査するときの順序:

1. `results/<名前>.checks.lock` の flock を取る｡取れなければ待たずに終了コード6
2. 前回の check.py が途中で止まって残した検査のプロセスがあれば止める (下の「孤児の掃除」)
3. 契約を照らす｡`tasks/<名前>.contract.sha256` がなければ終了コード4 (契約がない)｡契約ファイルがない･sha256 が違う･形式が不正なら終了コード5
4. worktree (`<ledger_root>/.worktrees/<名前>`) の `head` と `dirty` を求める (下の「状態の求め方」)
5. protected_paths を照らす (§4.7)｡違反があれば､検査を実行せずに不合格とする
6. `checks` を順に実行する
7. もう一度 `dirty` を求め､手順4と違えば警告を記録する (検査がファイルを変えた)
8. `results/<名前>.checks.jsonl` に1行を追記する
9. 差し戻しの回数を数えて終了コードを決める

ロックは手順1から9まで持つ｡回数の読み取りと追記は同じロックの下で行う｡

状態の求め方:

- `head` は `git -C <worktree> rev-parse HEAD`
- `dirty` はファイルの中身を含む tree の hash である｡一時 index を使い､`GIT_INDEX_FILE=<一時 index> git -C <worktree> add -A` の後に `GIT_INDEX_FILE=<一時 index> git -C <worktree> write-tree` の出力を使う
- 一時 index は worktree の外 (台帳の `results/` の下か､一時ディレクトリ) に置き､終わったら消す｡worktree の中に置くと､一時 index 自身が未追跡ファイルとして `add -A` に入る
- 速さのために､`git -C <worktree> rev-parse --git-path index` が示す本物の index を一時 index にコピーしてから `add -A` する｡空の index から始めると､全ファイルを hash し直す
- 本物の index は変えない｡ただし `add -A` は共有の object DB に loose object を書く｡これは `git gc` で片付く
- gitignore されたファイルの変更は `dirty` に入らない
- `core.gitutil.git` に環境変数を渡す引数 `env` を足して使う

検査の実行:

- `sh -c <cmd>` を worktree を作業ディレクトリにして実行する｡`start_new_session=True`､`stdin=DEVNULL`､環境変数に `CI=1` を足す
- 出力はパイプでなく一時ファイルに書かせる (標準出力と標準エラー出力をまとめる)｡`tail` は末尾2000文字
- `timeout` を過ぎたら､`os.killpg` でプロセスグループに SIGTERM を送る｡5秒待っても終わらなければ SIGKILL を送る｡その検査は exit 124 の不合格とする
- 検査が終わった後も､そのプロセスグループに残ったプロセスには SIGKILL を送る｡もう誰もいなければ ProcessLookupError になるので無視する
- check.py 自身が SIGTERM を受けたら､実行中の検査のプロセスグループを kill して終了コード143で終わる｡記録は書かない

孤児の掃除:

- 検査を始めるたびに､`results/<名前>.checks.pgid` にプロセスグループの ID と `cmd` を JSON で書き､検査が終わったら消す
- check.py が SIGKILL で止まると (Bash ツールの強制終了など)､検査は新しいセッションにいるので外側の kill が届かず､孤児として残る｡このときファイルも残る
- 次の check.py は､ロックを取った後にファイルが残っていれば､`ps -o pgid=,command= -p <ID>` で確かめる｡プロセスがあって pgid が記録と同じで､command が `sh -c <記録の cmd>` で始まるときだけ､そのプロセスグループに SIGKILL を送る｡その後ファイルを消し､warnings に記録する｡ID が使い回されていても､無関係なプロセスを止めないためである

jsonl の1行:

```json
{"ts": "…", "by": "main", "ok": false, "head": "<sha>", "dirty": "<tree の hash>",
 "checks": [{"name": "test", "cmd": "npm test", "exit": 1, "seconds": 12.3, "tail": "…"}],
 "protected_violations": ["infra/main.tf"], "warnings": [], "same_state": false}
```

`--reject` の行は `{"ts","by":"main","ok":false,"head","dirty","reject":"<理由>","same_state"}`､`--extend` の行は `{"ts","by":"human","extend":N,"agreed":"…"}` である｡

`--reject` と `--extend` の前提:

| | ロック | 契約 | worktree |
|---|---|---|---|
| `--reject` | `checks.lock` を取る (取れなければ6) | 必要 (なければ4､照合の失敗は5) | 必要 (なければ5) |
| `--extend` | `checks.lock` を取る (取れなければ6) | sha256 ファイルが必要 (なければ4) | 不要 |

標準出力には要約を出す｡検査ごとの合否と､不合格の検査の tail の末尾数行を含める｡`--by main` のときは､実行したコマンドの一覧も出す｡`same_state` が真なら､そのことも出す｡終了コード5のときは､理由 (契約の書き換え･worktree がない･git の失敗) を標準エラー出力に分けて書く｡

### 4.6 差し戻しの回数と上限

- 回数は､jsonl の `by=main` かつ `ok=false` の行について､`(head, dirty)` の異なる組の数で数える｡同じ状態の worktree で何度実行しても1回と数える｡メインの記憶には頼らない
- 上限は `max_returns + Σextend` である｡Σextend は `by=human` の行の `extend` の合計
- `--by main` の不合格または `--reject` で､回数が上限以上になったら終了コード3｡上限未満なら終了コード1で､メインは差し戻してよい
- つまり､差し戻せるのは `上限 - 1` 回までである｡既定の `max_returns=3` なら差し戻しは2回で､3回目の不合格で人に尋ねる
- 人が続けると答えたら､メインは `check.py --extend <N> --agreed "<人の返答>"` を実行してから差し戻す
- `by=worker` の行は数えない｡合格の行で回数は戻らない
- `--by worker` は0か1だけを返す｡上限を超えていても3は返さない
- 契約のないワーカー (調査だけの仕事) には上限がない｡メインは check.py を呼ばずに差し戻す (§5.3)

`dirty` はファイルの中身を含むので､未コミットのファイルの中身を直しただけでも状態が変わり､回数は正しく数えられる｡それでも､ワーカーは報告の前に必ず commit する (worker-rules.md)｡片付けのときに未コミットの変更が残らないようにするためである｡`--by main` のときに未コミットの変更があれば､check.py は要約に警告を出す｡

同じ状態の不合格:

- `by=main` の不合格 (`--reject` を含む) の `(head, dirty)` が､直前の `by=main` の不合格と同じなら､その行の `same_state` を真にする
- ワーカーが何も変えずに報告すると (直せなかった､commit を忘れたなど) こうなる｡回数は増えないので､差し戻しを繰り返しても上限に届かない
- そこでメインは､`same_state` が真なら差し戻しの文面を送らず､「変更が見当たりません」とワーカーに1回だけ伝える｡2回続けて `same_state` が真なら､人に尋ねる

### 4.7 protected_paths の照合

- git は `git -C <worktree>` で実行する
- 比べる基点は `git merge-base <base_ref> HEAD`｡`base_ref` が空か､merge-base が取れないときだけ `base` を使う
- コミット済みの変更: `git diff --no-renames -z --name-only <基点> HEAD`
- 未コミットの変更: `git status --porcelain=v1 -z --no-renames --untracked-files=all` のパス (未追跡を含む)｡`--no-renames` を付けるので､1つの項目のパスは常に1つである
- 各パスを `PurePosixPath(p).full_match(pat)` (Python 3.13 以降｡手元は 3.15) で照らす｡`*` は1階層の中だけに一致する｡`**` は先頭や途中では0階層以上に､末尾では1階層以上に一致する
- 照合は大文字と小文字を区別する｡macOS のファイルシステムが区別しなくても､`Infra/x` は `infra/**` に一致しない
- protected_paths のどれかに一致し､allow_paths のどれにも一致しないパスを違反とする

| パターン | パス | 一致 |
|---|---|---|
| `infra/**` | `infra/x/y` | する |
| `infra/**` | `infra` | しない |
| `*.lock` | `a/b.lock` | しない |
| `**/*.lock` | `x.lock` | する |
| `**/*.lock` | `a/b.lock` | する |
| `a/**/b` | `a/b` | する |
| `infra/**` | `Infra/x` | しない |

ルート直下だけでなく全階層のロックファイルを守るなら､`**/*.lock` と書く｡

### 4.8 改ざんへの備えの範囲

ワーカーは台帳ディレクトリを `--add-dir` で渡されているので､契約･sha256･jsonl を書き換えられる｡この設計で守るのは､ワーカーが事故で書き換えたときに気づけることだけである｡意図的な改ざんは防がない｡

- 契約が書き換わると､sha256 が合わなくなり､check.py は検査を実行せずに終了コード5を返す
- 契約の有無は､sha256 ファイルの有無で判断する｡契約ファイルだけを消しても「契約がない」にはならず､終了コード5になる
- ワーカーから `--by main`･`--reject`･`--extend` は呼べない (環境変数 `CONDUCTOR_ROLE` で判断する)
- finish.py も､`CONDUCTOR_ROLE=worker` のときは終了コード2で拒む｡ワーカーが `--skip-checks` で自分を片付けることを防ぐためである
- `--by main` の要約に実行したコマンドの一覧が出るので､メインと人は何が実行されたかを見られる

### 4.9 誰がいつ実行するか

- **ワーカー**: 完了を報告する前に commit し､`check.py --by worker` を実行する｡不合格なら直してから報告する｡回数には数えない｡直せないとき､または自分の担当の外が原因のときは､不合格の検査名を添えて行き詰まりとして報告する｡check.py が4･5･6を返したら､そのままメインに報告する
- **メイン**: 完了の報告を受けたら `check.py --by main` を実行し､受け入れ基準 (§5) と合わせて判断する｡結果の扱いは §6.2 に従う｡tail には秘密が含まれうるので､ワーカーに全文は返さない
- **finish.py**: 検査を再実行しない (§4.10)

check.py を Bash ツールで実行するときは､メインもワーカーも timeout を600000ミリ秒 (上限) に固定する｡検査の timeout の合計は540秒以下なので､必ず収まる｡合計が120秒を超える契約では､`run_in_background` で実行する｡

検査が gitignore されていないファイル (例: `coverage/`) を作ると､手順7で警告が出て､worktree に未コミットの変更が残る｡このままでは finish.py が未コミットの変更で止まる｡メインはこの警告を見たら､ワーカーにそのパスを .gitignore に足すか片付けるよう頼む｡人には聞かない｡ワーカーは､検査が作った生成物を commit しない｡

### 4.10 finish.py の照合

`--status 完了|PR作成済み` のとき､worktree が残っていて､それが自分のワーカーのものと確かめられた場合だけ照合する｡照合は `core/check.py`､片付けは `core/finish.py` が行い､タブの確認と閉じる操作は端末の層 (`tab_label`･`close_tab`) を通す｡

1. 未コミットの変更を確かめる (従来どおり｡あれば終了コード1)
2. `results/<名前>.checks.lock` を取る｡取れなければ終了コード6
3. 照合する｡次のどれかに当たれば､何も片付けずに終わる
   - sha256 ファイルがない (終了コード4)
   - 契約の sha256 が違う (終了コード5)
   - `by=main` の行 (`--reject` の行を含む) がない (終了コード5)
   - 最後の `by=main` の行が不合格 (終了コード5)
   - 最後の `by=main` の行の `head` か `dirty` が今の worktree と違う (終了コード5)
4. 片付ける (従来どおり)｡ロックは片付けが終わるまで持つ

- 人が明示したときだけ `--skip-checks` で照合を飛ばせる｡飛ばしたことを JSON の `warnings` に書く
- `--status 中止` のとき､worktree がないとき､自分のものと確かめられないときは照合しない
- この機能より前に起動した worktree のワーカーには契約がないので､終了コード4になる｡このときだけ､メインは人に一度聞いて `--skip-checks` を付ける
- `CONDUCTOR_ROLE=worker` のときは､何もせずに終了コード2で終わる

### 4.11 承認について

この節は Claude Code に固有の内容で､エージェントの層 (SKILL.md) に書く｡メインやワーカーが check.py や finish.py を実行すると､auto mode の判定で承認を求められることがある｡SKILL.md で､人に次の2つを `permissions.allow` に足すよう案内する｡ユーザーの settings.json の `permissions` は､メインとワーカーの両方に適用される｡

```
Bash(python3 ~/.claude/skills/conductor/check.py *)
Bash(python3 ~/.claude/skills/conductor/finish.py *)
```

これを許可すると､契約の `checks` の `cmd` が `sh -c` で承認なしに実行される｡`cmd` の中身は既定と差分への人の合意で決まるので､人が合意するときに中身を見ることが前提になる｡パターンはコマンドの文字列にそのまま照合されるので､メインもワーカーも check.py と finish.py を `~/.claude/skills/conductor/` つきのパスで呼び､引数を必ず1つ以上付ける (ワーカーは `--by worker`)｡スキルを別の場所に入れたときは､コマンドとパターンの両方に同じパスを使う｡

## 5. 受け入れ基準の質問

### 5.1 対象

起動の前に受け入れ基準を質問するのは､設計を伴う仕事だけである｡次のどれかに当たれば､設計を伴うとみなす｡

- 方式･構成･データ構造･画面の振る舞い･公開インターフェース (API やコマンドの引数など) のどれかを選ぶ必要があり､どれを選ぶかで人が完成とみなすかが変わる
- 完成形が複数ありえて､どれを選ぶかで人の評価が変わる
- 依頼から､完了の条件を1文で言えない

上の3つは､変えるファイルが決まっているかどうかより優先する｡「`src/cache.ts` にキャッシュを足して」はファイルが決まっていても､保持の時間や捨て方を選ぶ必要があるので聞く｡

次のときは聞かない｡

- 依頼文､指示書､リポジトリの既存の慣習から答えが1つに決まる
- 人がすでに基準を言っている
- やることと変えるファイルが決まっていて､選ぶものがない

### 5.2 聞き方と残し方

- 質問の手順は SKILL.md (エージェントの層) に書く
- メインは AskUserQuestion ツールで､受け入れ基準を最大3問まで聞く｡AskUserQuestion は､選択肢つきの質問を画面に出す Claude Code の機能である｡各問に候補を2〜4個示す
- 既定の合意 (§4.2) と差分の合意 (§4.4) も､同じターンの同じ AskUserQuestion にまとめる｡1回の呼び出しで聞ける数 (4問) を超えない範囲で､人への確認を1回にする
- 人が選んだ答えを､指示書の `## 受け入れ基準` に書く｡notes.md に `[決定]` で残す
- 受け入れ基準のうちコマンドで判定できるものは､人に聞いたうえで､そのワーカーの契約の `add_checks` にしてよい (§4.3)｡この質問は同じ AskUserQuestion の3問に含めてよい
- 設計を伴わない仕事でも､指示書には `## 受け入れ基準` を書く｡中身はメインが依頼から書き､人には聞かない

### 5.3 完了の判断

メインはワーカーの完了報告を受け入れ基準に照らし､`check.py --by main` の合格と合わせて判断する｡検査は合格したが基準に足りない点があれば､その点を具体的に挙げて `check.py --reject "<足りない点>"` を実行し､ワーカーに返す｡`--reject` も差し戻しの回数に数える (§4.6)｡

契約のないワーカー (`--worktree` なしで起動した調査の仕事) には check.py を呼ばない｡受け入れ基準に足りない点を SendMessage で返すだけで､回数は数えない｡

## 6. 終了コードとメインの扱い

### 6.1 終了コード

| コード | check.py | finish.py | spawn.py |
|---|---|---|---|
| 0 | 合格｡`--extend` は記録できた | 片付けた | 起動した |
| 1 | 不合格 (`--reject` を含む)｡差し戻してよい | 実行時の失敗 (未コミットの変更､git の失敗など) | 実行時の失敗 (既定の照合の失敗･追跡済みの既定･下書きの不備を含む) |
| 2 | 使い方の誤り (argparse の誤り､ワーカーからの禁じられた呼び方､`--extend` の値の誤り) | 使い方の誤り (argparse､ワーカーからの実行) | 使い方の誤り (argparse) |
| 3 | 不合格で､差し戻しの上限に達した (`--by worker` は返さない) | - | - |
| 4 | 契約がない (sha256 ファイルがない) | 契約がない | 既定も既定の下書きもない |
| 5 | 契約の照合の失敗 (契約ファイルがない･sha256 の不一致･形式の不正･worktree がない･git の失敗) | 合格の記録の照合の失敗 (§4.10 の手順3) | - |
| 6 | ロックを取れない (別の check.py が実行中) | ロックを取れない | - |
| 143 | SIGTERM を受けて止まった (記録なし) | - | - |

argparse の誤りは argparse 既定の2のままにし､ほかの理由と番号が重ならないようにする｡`CONDUCTOR_HOST`･`CONDUCTOR_AGENT` が未知の値なら､spawn.py･finish.py･init_main.py は2で終わる (§L.3)｡init_main.py の終了コード (2･3) は従来どおりである｡

### 6.2 メインの扱い

| スクリプトと終了コード | メインの動き |
|---|---|
| check.py 0 | 受け入れ基準と照らす｡足りなければ `--reject` (§5.3) |
| check.py 1 (検査の不合格) | 失敗した検査の名前と tail の数行だけを SendMessage で返す |
| check.py 1 (protected_paths の違反) | 違反したパスを SendMessage で返す |
| check.py 1･3 で `same_state` が真 | §4.6 の「同じ状態の不合格」に従う |
| check.py 3 | 差し戻さず人に尋ねる｡続けるなら `--extend` を実行してから差し戻す |
| check.py 4･5 | 人に理由を伝える |
| check.py 6 | 10秒ほど待ってから1回だけやり直す｡また6なら人に伝える |
| check.py 143 | 1回だけ実行し直す｡また143なら人に伝える |
| finish.py 1 (未コミットの変更) | ワーカーに commit を頼む (従来どおり) |
| finish.py 4 | 機能の導入前のワーカーなら､人に一度聞いて `--skip-checks` を付ける｡それ以外は人に伝える |
| finish.py 5 (記録がない･最後が不合格･head か dirty の不一致) | `check.py --by main` を実行し直す｡合格なら finish.py をもう一度呼ぶ｡不合格なら差し戻しの流れに入る |
| finish.py 5 (契約の sha256 の不一致) | 人に理由を伝える |
| finish.py 6 | 10秒ほど待ってから1回だけやり直す｡また6なら人に伝える |
| spawn.py 4 | 人に失敗を伝えず､既定の合意の手順 (§4.2) に戻る |
| spawn.py 1 で「既定と下書きの両方がある」 | 下書きを消して起動し直す (§4.2 の手順6) |

## 7. 変える既存の部品

先に3層へ組み替え (§L)､その上に機能を足す｡

- 組み替え: `conductor_lib.py` を `core/ledger.py`･`core/gitutil.py`･`hosts/herdr.py` に分ける｡spawn.py･finish.py･init_main.py･reinject.py の手順を `core/` に移し､ルートのスクリプトはエントリポイントだけにする｡`layers.py`･`core/errors.py`･`core/protocols.py`･`hosts/herdr.py`･`agents/claude.py` を作る｡`herdr-plugin/` を `hosts/herdr-plugin/` に移し､herdr のプラグインの登録をやり直す｡init_main.py に `--probe` を足す
- core/ledger.py: `file_lock`･`create_exclusive` を足す
- core/gitutil.py: `git()` に環境変数を渡す引数 `env` を足す
- core/contract.py と core/check.py を作る｡check.py はエントリポイント
- core/spawn.py: 既定の下書きの取り込み､既定の照合､ワーカーの契約の作成､`CONDUCTOR_CONTRACT`･`CONDUCTOR_HOST`･`CONDUCTOR_AGENT` を `create_tab` の `env` に入れる､既定がないときの終了コード4
- core/finish.py: 片付け前の照合､`--skip-checks`､ワーカーからの実行の拒否
- core/reinject.py: メインに notes.md も出す｡予算を文字数に変え､標準出力の全体を `hook_output_limit - 1000` 文字以内にする (ワーカーの出力も)
- agents/claude.py: ワーカーの起動の指定 (`launch`)､statusline の読み取りと表示､フックの入力と出力の形式､GUIDE と clear の文､`hook_output_limit`
- SKILL.md (エージェントの層): notes.md の書き方､既定と差分の合意､受け入れ基準の質問､検査と差し戻し､終了コードごとの扱い (§6.2)､承認の案内､「メインが書いてよいもの」に notes.md･下書き･checks.jsonl (check.py 経由) を足す｡check.py と finish.py の呼び出しをすべて `~/.claude/skills/conductor/` つきのパスにする｡herdr のコマンド名と `HERDR_ENV` の確認を除き､端末の中で動いているかは `init_main.py --probe` で確かめる｡終了コード2の説明に「未知の層」を足す
- worker-rules.md (エージェントの層): 報告前の commit と検査､行き詰まりの報告､protected_paths に触れない､生成物を commit しない､契約･sha256･jsonl を変えない｡herdr の名前を除く
- DESIGN.md: §4 のデータ構成に notes.md･既定･契約･sha256･checks.jsonl･checks.pgid を足す｡コードの3層の構成を足し､DESIGN-3 §L を参照と書く

## 8. 確かめ方

検証には DESIGN.md §8 と同じ検証用のリポジトリを使う｡検査を確実に不合格にするには､契約の checks に `test -f PASS` を入れ､worktree に `PASS` があるかで合否を切り替える｡

1. 新しいセッションの画面下部に `ctx NN%` が出る
2. メインで compact した後､notes.md の最後の行を答えられる｡大きな tasks.md と notes.md でも､フックの出力がファイルに退避されず (プレビュー表示にならず) 文脈に入る
3. 既定がない状態で worktree のワーカーを起動すると､メインが8行以内で既定を聞く｡2人目は聞かずに起動し､1行で知らせる
4. `PASS` を消して `test -f PASS` を不合格にすると差し戻しになり､3回目の不合格で人に尋ねる｡`--extend 1` の後はもう1回差し戻せる
5. ワーカーが何も変えずに報告すると､`same_state` が出て､メインは同じ差し戻しを送らない
6. protected_paths のパスを変えると､検査を実行せずに不合格になる
7. 契約ファイルを書き換えると､check.py が終了コード5を返す
8. 最後の `--by main` の合格の後に commit すると､finish.py が終了コード5を返す｡メインは check.py を実行し直してから片付ける
9. `sleep 300` の検査を入れた check.py を､Bash ツールの短い timeout で止める｡次の check.py の実行で `sleep` が止まり､warnings に記録される
10. 設計を伴う依頼で､メインが AskUserQuestion で受け入れ基準を聞き､指示書と notes.md に書く
11. 組み替えの後も､検証用のリポジトリでの起動･報告･compact の後の再注入･片付けが組み替えの前と同じに動く｡`CONDUCTOR_HOST=unknown` で spawn.py が終了コード2を返す｡`hosts/herdr-plugin/` に移したプラグインから承認待ちの通知が届く
