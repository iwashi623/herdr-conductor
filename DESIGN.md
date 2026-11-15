# conductor 設計書

作成: 2026-11-06｡対象環境: macOS (Apple Silicon)､zsh､herdr 0.9.5､Claude Code 2.1.319｡

## 1. なぜ作るか

herdr 上で複数の Claude Code セッションを動かしていると､人がタブやペインを渡り歩いて指示や承認をすることになり､それが疲れる｡そこで､人は基本的に1つの｢メイン｣セッションと話し､メインが名前付きの｢ワーカー｣セッションを herdr のタブに自動で起動して仕事を任せる形にする｡

人はワーカーと直接話してもよい｡メインとワーカーのどちらとも話せることが重要である｡メインは compact を挟んでも仕事の状態を失わず､長く動き続ける｡状況の確認には herdr のサイドバーを使う｡

## 2. ゴール･非ゴール･失敗の条件

ゴール:

1. メインが名前付きのワーカーを起動できる｡ワーカーは herdr のサイドバーと Claude Code のセッション一覧の両方に同じ名前で出る
2. 人はメインとも各ワーカーとも直接話せる｡人がワーカーに直接出した指示で方針が変わったら､メインはそれを知る
3. メインは compact の後も､どのワーカーに何を任せたかを失わない
4. サイドバーで､ワーカーの名前･状態･担当タスクが分かる
5. メインは複数同時に動かせる｡同じリポジトリでも､別々のリポジトリでもよい
6. コードを変えるワーカーは､専用の git worktree とブランチで作業し､最後に PR を作る

非ゴール (今回は作らない):

- メインを自動で作り直す仕組み (compact と台帳の再読込で足りる見込み)
- メインがワーカーの承認ダイアログに答える仕組み
- ワーカーの会話全文をメインに中継する仕組み
- ワーカーが課題管理から自分でタスクを取りに行く仕組み
- herdr-core (hc)､Herdr App､Claude Code の agent teams の利用

失敗の条件 (動いても失敗とみなすもの):

- 人がメインに入力している最中に､報告などの文字が入力欄に割り込む
- メインがワーカーの承認を代わりに押す
- 仕事の状態がメインの会話の中にしかなく､compact で失われる
- 人の操作なしにワーカーが main ブランチへ直接マージする

## 3. 構成

```
herdr ワークスペース (メインごとに1つを推奨)
 ├─ タブ: メイン ─ Claude (/conductor で指揮役になる)
 ├─ タブ: auth-fix ─ Claude ワーカー
 └─ タブ: docs-update ─ Claude ワーカー

メイン ⇄ ワーカー: Claude Code のセッション間メッセージ (SendMessage､notify_when_idle)
起動･タブ･サイドバー: herdr CLI (spawn.py 経由)
状態の正本: <リポジトリ>/.orchestra/<メイン名>/
```

部品と置き場所:

| 部品 | 置き場所 | 役割 |
|---|---|---|
| 指揮役スキル | `~/.claude/skills/conductor/SKILL.md` | メインの手順書｡`/conductor` で読み込む |
| メイン登録スクリプト | `~/.claude/skills/conductor/init_main.py` | メインの台帳を作り､main.json を書く｡`--retire` で消す |
| 起動スクリプト | `~/.claude/skills/conductor/spawn.py` | ワーカーを1人起動する｡詳細は 5.1 |
| 片付けスクリプト | `~/.claude/skills/conductor/finish.py` | ワーカーのタブと worktree を片付ける｡詳細は 5.6 |
| ワーカー規則 | `~/.claude/skills/conductor/worker-rules.md` | `--append-system-prompt-file` でワーカーに渡す規則 |
| compact 後の再読込フック | `~/.claude/skills/conductor/reinject.py`､`~/.claude/settings.json` に登録 | 詳細は 5.5 |
| 承認待ちの通知 | herdr 設定 (`~/.config/herdr/config.toml`) か herdr プラグイン | 詳細は 5.4 |
| herdr 連携 | `herdr integration install claude` | v7 から v11 に更新｡compact 後のセッション ID を herdr に追わせる |

## 4. データ

台帳の置き場所は､メインの作業ディレクトリが属する git リポジトリのルートとする｡git 管理外なら作業ディレクトリそのものとする｡

```
.orchestra/
  <メイン名>/
    main.json          メインのセッション名と herdr のペイン ID
    tasks.md           台帳｡ワーカーごとに1行 (名前･担当･状態･ブランチ･PR･最終更新)
    tasks/<ワーカー名>.md    ワーカーへの指示書
    results/<ワーカー名>.md  ワーカーの成果報告
    notes.md           メインの記録｡追記だけで書き換えない｡compact の後に tasks.md と一緒に読み込み直される
    tasks/<ワーカー名>.contract.json           契約 (検査･protected_paths･差し戻しの上限)
    tasks/<ワーカー名>.contract.sha256         契約の sha256
    tasks/<ワーカー名>.contract.draft.json     差分の下書き (既定から外れるときだけ)
    results/<ワーカー名>.checks.jsonl          check.py の実行の記録
    results/<ワーカー名>.checks.lock           check.py と finish.py が取るロック
    results/<ワーカー名>.checks.pgid           実行中の検査のプロセスグループ
  contract-defaults.json         リポジトリごとの既定の契約 (<メイン名> の1つ上)
  contract-defaults.sha256       既定の sha256
  contract-defaults.draft.json   既定の下書き (spawn.py が読んで既定に変える)
.worktrees/<ワーカー名>/    コードを変えるワーカーの作業ディレクトリ
```

- notes.md と契約と検査の詳細は DESIGN-3.md を参照する (§3.2､§4)
- `.orchestra/` と `.worktrees/` は `.git/info/exclude` に追記して git の管理から外す｡リポジトリの `.gitignore` は変えない
- メイン名は､メインの Claude Code セッション名 (`ListAgents` が｢This session is ○○｣と返す名前) を使う
- tasks.md の書き手はメインとする｡ワーカーは自分の行の｢状態｣だけを更新してよい
- 状態の値: `作業中`､`承認待ち`､`人と対話中`､`完了`､`PR作成済み`､`中止`

### コードの構成

コードは3層に分ける｡振る舞い (`core/`)､端末 (`hosts/`)､エージェント (`agents/`) である｡core は端末とエージェントを import せず､引数で受け取る｡`SKILL.md` と `worker-rules.md` はエージェントの層の文書で､スクリプトだけを呼ぶ｡

エントリポイントはルートの `spawn.py`･`finish.py`･`init_main.py`･`check.py`･`reinject.py`･`statusline.py` で､層は `layers.py` が選ぶ｡TerminalHost と Agent の Protocol は DESIGN-3.md の §L を参照する｡

## 5. 動き

### 5.1 ワーカーの起動 (spawn.py)

```
spawn.py --main <メイン名> --name <ワーカー名> --task-file <指示書のパス> [--worktree] [--model <モデル>]
```

1. `HERDR_ENV=1` でなければ失敗する
2. ワーカー名が `[a-z][a-z0-9_-]{0,31}` に合うかを確かめる｡次の3つのどれかに同じ名前があれば失敗し､メインに別名を求めさせる (実装者の判断で決めた点)
   - herdr の `agent list` の生きたエージェント
   - `.worktrees/<名前>` ディレクトリ
   - ブランチ `conductor/<名前>`
3. `--worktree` のとき､`git worktree add .worktrees/<名前> -b conductor/<名前>` を実行する｡初回は `.git/info/exclude` に追記する
4. `herdr tab create` で､メインのワークスペースにワーカー名のタブを作る｡フォーカスは移さない
5. `herdr agent start <名前> --kind claude --pane <タブのペイン> -- --name <名前> --permission-mode auto --append-system-prompt-file worker-rules.md "<短い起動文>"` を実行する
   - `--permission-mode auto` は既定と同じだが､メインと権限モードの系統をそろえるために明示する
   - herdr の agent 名はサイドバーに､`--name` は Claude Code のセッション一覧に出る｡2026-11-06 の実験 (`hdtest`) で両方に同じ名前で出ることを確認済み
   - 起動文は｢指示書 <パス> を読んで作業を始めてください｡報告先は <メイン名> です｣程度に短くする｡macOS は1行の長さに上限があり､長い文を渡すと拒否されるため
6. `herdr pane report-metadata` で､サイドバーの作業中の文言をタスクの要約 (80字以内) にする｡表示されなくてもゴール1は agent 名で満たせるので､失敗しても起動は止めない
7. tasks.md に行を追加する
8. 起動に失敗したら (フォルダ信頼の確認で止まった場合を含む) ､画面の内容を出して失敗を返す｡確認ダイアログには答えない
9. 結果を JSON で標準出力に出す (タブ ID､ペイン ID､作業ディレクトリ､ブランチ)

### 5.2 指示と報告

2026-11-06 の実験で確認済みの事実:

- herdr の `agent start` で起動したワーカーは `ListAgents` に名前で出る｡人が手で起動したメインとの間で､`SendMessage` の往復と `notify_when_idle` の通知が届いた
- メッセージは受け手の入力欄には入らない｡受け手の画面には｢Message from @送り手｣の1行が出る｡公式ドキュメントによると､受け手が作業中ならツール実行の合間に読まれ､待機中なら新しいターンが自動で始まる
- 送り手と受け手の権限モードの系統が違うと､メッセージは保留される｡そのとき送り手には `[Cross-session delivery notice]` が届くので､メインはそれを受けたら人に伝える

手順:

- `/conductor` の最初の手順で､メインは `ListAgents` で自分のセッション名を読み､`$HERDR_PANE_ID` と一緒に main.json に書く｡`/conductor` を再実行したら書き直す
- メインはワーカー起動後に `SendMessage(to=<ワーカー名>, notify_when_idle=true)` で通知を予約し､ターンを終える｡ポーリングはしない
- 追加の指示や質問も `SendMessage` で送る｡herdr の `agent prompt` やキー送信は､入力欄に文字を打つので使わない
- ワーカーは､完了･行き詰まり･人からの方針変更の3つの場面で､メインに `SendMessage` で報告する｡長い内容は results/ に書き､報告にはパスと1〜2行の要約だけを書く

### 5.3 人がワーカーと直接話したとき

ワーカーは､人から直接受けた指示で担当範囲･方針･成果物が変わったら､tasks.md の自分の行を更新し､メインに1行で報告する｡会話の全文は送らない｡これは worker-rules.md の規則で実現し､自動の仕組みは作らない｡

### 5.4 承認待ち

ワーカーは auto mode で動く (Claude Code の既定)｡それでも残る承認待ちには､人がワーカーのタブで直接答える｡メインは承認を代行しない｡

人への知らせ方は次の順に試し､先に要件を満たしたほうを採用する｡

1. herdr の既定機能: `[ui.toast] delivery = "system"` と `[ui.sound]`｡ただし既定設定の説明は｢バックグラウンドのワークスペース｣での状態変化が対象と読める｡ワーカーはメインと同じワークスペースのタブにいるので､通知が出るかを実機で確かめる
2. 出なければ herdr プラグイン: `[[events]] on = "pane.agent_status_changed"` で状態が `blocked` のときに `herdr notification show --sound request` を呼ぶ｡参考: ogulcancelik/herdr-plugin-examples の agent-telegram-notify

### 5.5 compact 後の再読込

`SessionStart` フックを matcher `compact` で登録する｡フックは自分の herdr ペイン ID (`HERDR_PANE_ID`) を手がかりに､自分がどのメインかワーカーかを判断する｡

- メインなら: 自分の tasks.md の中身と｢あなたは conductor の指揮役です｡`/conductor` の手順に従ってください｣の1行を出力する
- ワーカーなら: 自分の指示書の中身を出力する (ワーカー規則はシステムプロンプトに入っているので compact で失われない)
- どちらでもなければ何も出力しない

ペイン ID と台帳の対応は main.json と tasks.md に記録する｡フックは Claude Code のプロセスから起動され､Claude Code は herdr のペインのシェルから起動されるので､`HERDR_PANE_ID` は引き継がれる見込み｡実機で確かめる｡渡らなかった場合の代わりの手段は実装者に任せる｡例えばワーカーには spawn.py が `--settings` で役割と台帳のパスを環境変数として渡し､メインは main.json のセッション ID で照合する｡作業ディレクトリでの照合は､メインとワーカーが同じディレクトリにいると区別できないので使わない｡

### 5.6 終わらせ方

- コードを変えるワーカーは､作業が終わったらブランチを push して PR を作る｡PR の作り方は､そのリポジトリの CLAUDE.md の決まりに従う (GitHub なら `gh pr create`)｡PR の URL をメインに報告する
- メインは PR の URL を tasks.md に記録し､人に伝える｡マージは人が行う
- 人が｢片付けて｣と言ったら､メインは finish.py を呼ぶ｡finish.py はワーカーのタブを閉じ､worktree を `git worktree remove` で消し､tasks.md の状態を更新する｡ブランチは消さない｡未コミットの変更がある worktree は消さずに失敗を返す

## 6. 制約と既知の問題

- herdr は､名前を指定した `agent prompt` が別のエージェントに届くことがある (Issue #4764)｡herdr の操作はペイン ID で行う
- herdr の状態判定は画面の照合で､承認待ちを見逃すことがある (Issue #5775)｡完了の判断にはサイドバーを使わず､メッセージと台帳を使う
- 初めて開くディレクトリで Claude を起動すると､フォルダ信頼の確認で止まる｡ただし信頼済みフォルダの下に新しく作ったディレクトリでは確認が出ないことを､2026-11-06 に確かめた｡`.worktrees/` を信頼済みのリポジトリの中に置けば止まらない｡リポジトリ自体が未信頼なら､人が一度だけそのリポジトリでメインを起動して信頼する｡`~/.claude.json` などの設定を書き換えて信頼させることはしない
- `herdr integration install claude` は herdr が管理するフック (`~/.claude/hooks/herdr-agent-state.sh`) と settings.json の登録を書き換える｡同じコマンドを再実行すれば元の管理状態に戻る
- セッション間メッセージは､送り手と受け手の権限モードの系統が違うと保留される｡メインとワーカーはどちらも auto mode で動かす
- ワーカーの起動は Claude Code のサブプロセスからでなく herdr 経由で行う｡hc の試験では､Claude Code の環境変数を引き継いだ子セッションが会話記録を保存しなかった｡herdr の `agent start` はペインのシェルから起動するので影響しない見込みだが､実機で確かめる

## 7. 実装者に任せること

- SKILL.md と worker-rules.md の文面
- tasks.md の具体的な書式 (表か箇条書きか)
- スクリプトの言語 (シェルか Python か)
- 5.4 の通知方式の最終選択

## 8. 確かめ方

検証には検証用のリポジトリ (push しない使い捨てのリポジトリか個人のフォーク) を使う｡共有のリポジトリには push も PR もしない｡PR は使い捨てのリポジトリか個人のフォークに作る｡検証が終わったら PR を閉じ､検証用のブランチを消す｡

1. 検証用のリポジトリで､メイン1つとワーカー2人 (worktree あり･なし) を起動し､サイドバーとセッション一覧の名前を確かめる
2. ワーカーの完了報告がメインに届き､メインの入力欄に文字が入らないことを確かめる｡メインの入力欄に書きかけの文がある状態で報告が届いたとき､書きかけの文が消えたり送信されたりしないことも確かめる
3. ワーカーのタブで人が方針を変え､メインに報告が届くことを確かめる
4. ワーカーを承認待ちにして､通知が出ることと､メインが承認しないことを確かめる
5. メインで `/compact` を実行し､直後に台帳の内容が文脈に入ることを確かめる
6. 同じリポジトリでメインを2つ動かし､台帳と報告先が混ざらないことを確かめる
7. worktree ワーカーが検証用のリポジトリに PR を作り､finish.py で片付けられることを確かめる
