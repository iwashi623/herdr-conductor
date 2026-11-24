# conductor

conductor は、複数のワーカー Claude を端末のタブで動かすための Claude Code スキルです。メインのセッションが、名前を付けたワーカーの Claude Code セッションを herdr のタブに起動し、仕事を任せます。ワーカーの状態は台帳(Markdown のファイル)に記録します。完了の報告を受けたら、検査で成果を確かめ、タブと worktree を片付けます。人はメインとも各ワーカーとも直接話せます。

## 必要なもの

- Claude Code。auto mode で動かし、`ListAgents` と `SendMessage` によるセッション間のメッセージが使えること
- herdr。プラグインの定義(`hosts/herdr-plugin/herdr-plugin.toml`)が要求する最低のバージョンは 0.7.0 です。動作は 0.9.5 で確かめています
- Python 3.13 以上。`PurePosixPath.full_match` を使っています。標準ライブラリだけで動き、追加のパッケージは要りません
- git

ワーカーは `--permission-mode auto` で起動します。また、リポジトリの `.claude/settings.local.json` にある MCP サーバーの承認(`enabledMcpjsonServers` など3つのキー)を、ワーカーの起動時に `--settings` で引き継ぎます。人がそのリポジトリで既に出した承認だけを渡し、ほかの許可は渡しません。

## 入れ方

スキルのディレクトリにクローンします。

```sh
git clone https://github.com/iwashi623/herdr-conductor.git ~/.claude/skills/conductor
```

承認待ちの通知を出す herdr のプラグインを登録します。

```sh
herdr plugin link ~/.claude/skills/conductor/hosts/herdr-plugin
```

次に、`~/.claude/settings.json` を編集します。compact か clear のあとに台帳の内容をメインへ入れ直す SessionStart フックは必須です。statusLine は任意で、コンテキストの使用率を画面の下に出します。

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "^(compact|clear)$",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/skills/conductor/reinject.py",
            "timeout": 10
          }
        ]
      }
    ]
  },
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/skills/conductor/statusline.py"
  }
}
```

すでに `hooks` や `statusLine` がある場合は、そこに足してください。

メインとワーカーが check.py と finish.py を承認なしで実行できるように、`permissions.allow` に次の2つも足します。

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 ~/.claude/skills/conductor/check.py *)",
      "Bash(python3 ~/.claude/skills/conductor/finish.py *)"
    ]
  }
}
```

このパターンはコマンドの文字列にそのまま照合されます。メインもワーカーも、必ず `~/.claude/skills/conductor/` の接頭辞でスクリプトを呼びます。スキルを別の場所に入れたときは、コマンドとパターンの両方で同じパスを使ってください。許可すると、検査の契約に書いた `checks` のコマンドも承認なしで動くので、契約の中身は人が合意の場で確かめます。

## 使い方

herdr のペインで Claude Code を起動し、そのセッションで `/conductor` と入力します。このセッションがメインになります。

1. メインに依頼を伝えると、メインが仕事をワーカー単位に分けます。設計を伴う仕事では、起動の前に受け入れ基準を質問されます
2. メインがワーカーを herdr のタブに起動し、指示書を渡します。コードを変える仕事のワーカーには、専用の git worktree を用意します
3. ワーカーが完了を報告したら、メインが検査を実行します。受け入れ基準に足りなければ、差し戻します。差し戻しの回数には上限があり、上限に達したら人に尋ねます
4. 合格した成果は、人が確かめてマージします。メインがマージすることはありません
5. 人が「片付けて」と指示すると、メインがワーカーのタブと worktree を片付け、台帳の状態を更新します

全体の手順は [SKILL.md](SKILL.md) にあります。

## 構成

コードは3つの層に分かれています。端末(herdr)とエージェント(Claude Code)を差し替えられるように、core はどちらにも依存しません。層の Protocol は [DESIGN-3.md](DESIGN-3.md) の §L にあります。

- core: 台帳、検査の契約、片付けなどの振る舞い。`core/`
- hosts: 端末の操作。herdr の実装と、承認待ちの通知プラグインがあります。`hosts/`
- agents: エージェントの実装と、Claude が読む手順書です。`agents/claude.py`、`SKILL.md`、`worker-rules.md`

エントリポイントは、ルートにある次のスクリプトです。

- `spawn.py`: ワーカーを起動します
- `finish.py`: ワーカーのタブと worktree を片付けます
- `init_main.py`: メインを台帳に登録します
- `check.py`: ワーカーの成果を契約の検査で確かめます
- `reinject.py`: compact や clear のあとに、台帳の内容をメインへ入れ直します(SessionStart フック)
- `statusline.py`: コンテキストの使用率を1行で出します。自動 compact の窓(`modelSettings.<モデル>.autoCompactWindow`)があればそれに対する割合、なければモデルのコンテキストウィンドウに対する割合で、`/context` の表示と揃います

設計の背景は [DESIGN.md](DESIGN.md) と [DESIGN-3.md](DESIGN-3.md) にあります。

## テスト

```sh
python3 -m unittest discover -s tests -t .
```

テストは偽の herdr を使うので、本物の herdr も claude も呼びません。

## ライセンス

[MIT](LICENSE)
