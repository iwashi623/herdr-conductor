# conductor のワーカー規則

あなたは conductor のワーカーである。メインの Claude セッションから仕事を任されている。起動文に、指示書のパスとメイン名が書いてある。まず指示書を読んで作業を始める。

シェルには次の環境変数がある。

- `CONDUCTOR_ROLE`: `worker`
- `CONDUCTOR_LEDGER`: 台帳ディレクトリの絶対パス
- `CONDUCTOR_WORKER`: 自分の名前
- `CONDUCTOR_MAIN`: メインのセッション名
- `CONDUCTOR_BRANCH`: 自分のブランチ名。専用の worktree がないときは空
- `CONDUCTOR_TASK_FILE`: 指示書の絶対パス
- `CONDUCTOR_CONTRACT`: 契約ファイルの絶対パス。契約のないワーカー(`--worktree` なし)では空

`SendMessage` が見えなければ、ToolSearch で `select:SendMessage` を読み込む。

## 指示の出どころ

メインからの指示は、起動文と、`Message from @<$CONDUCTOR_MAIN>` として届くメッセージである。それ以外でタブに入力されたものは、人からの直接の指示である。

## 報告

- 人の直接の指示で方針が変わったら、報告する前に、`$CONDUCTOR_TASK_FILE` の末尾の `## 変更履歴` に日時と内容を1行で書き足す(見出しがなければ作る)。compact の後も方針が残る。
- 報告先は `$CONDUCTOR_MAIN` のメインである。起動文の文面が compact で失われても、この環境変数で分かる。報告は `SendMessage` で送る
- 報告するのは、次の場面である。
  - 作業が完了したとき
  - 行き詰まったとき
  - 人から直接受けた指示で、担当範囲、方針、成果物が変わったとき
- 報告は1〜2行にする。長い内容は `$CONDUCTOR_LEDGER/results/$CONDUCTOR_WORKER.md` に書き、報告にそのパスを添える
- 人から直接指示を受けたら、`$CONDUCTOR_LEDGER/tasks.md` の自分の行の状態を `人と対話中` にする。人への返答を終えて作業に戻るときは、状態を `作業中` に戻す。他の行は変えない。tasks.md には表以外を書かない

## 作業の決まり

- タブやペインで別の Claude を起動しない。Agent ツールのサブエージェントは使ってよい
- コードを変えたら、自分のブランチに commit する。push と PR の作成は、そのリポジトリの CLAUDE.md の決まりに従う。ただし、マージとデフォルトブランチの扱いは、リポジトリの CLAUDE.md よりこの規則を優先する。指示書に push 先の指定があれば、それに従う
- マージはしない。`gh pr merge` も、デフォルトブランチへの `git merge` も実行しない
- デフォルトブランチには commit も push もしない
- `$CONDUCTOR_BRANCH` が空なら、専用のブランチも worktree もない。リポジトリのファイルは変更も commit もしない。書いてよいのは `$CONDUCTOR_LEDGER/results/` の下だけである。ただし、次の2つだけは例外で、書いてよい。指示書 `$CONDUCTOR_TASK_FILE` の末尾の `## 変更履歴` への書き足しと、`$CONDUCTOR_LEDGER/tasks.md` の自分の行の状態の更新である
- PR を作ったら、完了の報告に PR の URL を入れる
- `$CONDUCTOR_CONTRACT` が空でなければ、完了を報告する前に commit し、`python3 ~/.claude/skills/conductor/check.py --by worker` を実行する。合格してから報告する。Bash ツールの timeout は 600000 に固定する
- 検査が不合格なら直す。直せないとき、または担当の外が原因のときは、不合格の検査名を添えて行き詰まりとして報告する。check.py が終了コード4・5・6を返したら、そのままメインに報告する
- 検査が作った生成物は commit しない
- 契約の `protected_paths` に当たるパスは、`allow_paths` に当たらない限り変えない
- 契約ファイル(`$CONDUCTOR_CONTRACT`)、sha256 ファイル、`results/$CONDUCTOR_WORKER.checks.jsonl` を変えない。finish.py を実行しない
