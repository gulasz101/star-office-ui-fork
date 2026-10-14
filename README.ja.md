# Star Office UI

🌐 言語: [中文](./README.md) | [English](./README.en.md) | **日本語**

![Star Office UI カバー](docs/screenshots/readme-cover-2.jpg)

**ピクセルアート AI オフィス ダッシュボード** —— AI エージェントの作業状態をリアルタイムで可視化。誰が何をしているか、昨日何をしていたか、オンラインかどうかを一目で確認できます。

マルチエージェント協働、3言語 UI（中/英/日）、AI 部屋デザイン、デスクトップペットモード対応。

**Agent-Agnostic（エージェント非依存）設計。** オフィスが話すのは 1 つのオープンな HTTP プロトコルだけ。リクエストを送れる AI エージェントなら誰でも-board に参加できます：Hermes、OpenChamber、OpenCode、Pi、Claude、自作スクリプトでも。バックエンドは特定のエージェントフレームワークに依存せず、そのワークスペースファイルを読むこともありません。

> 本プロジェクトは **[Ring Hyacinth](https://x.com/ring_hyacinth)** と **[Simon Lee](https://x.com/simonxxoo)** が創作。このリポジトリは其のメンテ分支（[gulasz101/star-office-ui-fork](https://github.com/gulasz101/star-office-ui-fork)）です：イベント DB 化、プロトコル統一、OpenClaw デカップリング完了。
> Issue / PR 歓迎 —— コントビューター全員に感謝します。

---

## ✨ クイックスタート

### オプション 1：AI エージェントにデプロイさせる

shell と HTTP が使えるエージェントなら誰でも設置できます。この文を送ってください：

```text
Star Office UI をデプロイして：
1. git clone https://github.com/gulasz101/star-office-ui-fork && cd star-office-ui-fork
2. python3 -m pip install -r backend/requirements.txt   （Python 3.10+ 必要）
3. cd backend && python3 app.py    # サーバー起動：http://127.0.0.1:19000
4. 自分の状態を POST /events で送信する。README の「Agent 接続」セクション参照。
```

エージェントが clone・依存インストール・起動を自動で行い、1 件目のイベントで自身が board に登録されます。

### オプション 2：30 秒の手動セットアップ

> **Python 3.10+ が必要**（`X | Y` ユニオン構文を使用、3.9 以前は非対応）

```bash
# 1) リポジトリを clone
git clone https://github.com/gulasz101/star-office-ui-fork.git
cd star-office-ui-fork

# 2) 依存インストール（Python 3.10+）
python3 -m pip install -r backend/requirements.txt

# 3) バックエンド起動 —— イベント DB は自動初期化
#    （デフォルト SQLite。PostgreSQL は DATABASE_URL=postgresql+psycopg2://... を設定）
cd backend
python3 app.py
```

**http://127.0.0.1:19000** を開き、状態を送ってみます：

```bash
curl -X POST http://127.0.0.1:19000/events \
  -H 'Content-Type: application/json' \
  -d '{"version":1,"agent":"hermes","event_type":"state_change",
       "timestamp":"2026-10-13T14:20:55Z","payload":{"state":"writing","detail":"ドキュメント整理中"}}'
```

> 🐳 コンテナがよければ: `docker run -p 127.0.0.1:19000:19000 ghcr.io/gulasz101/star-office-ui-fork:latest`
> を実行して同じアドレスを開いてください。公開イメージは起動時に自らマイグレーションします
> （詳細は下の「Docker デプロイ」参照）。

![Star Office UI プレビュー](docs/screenshots/readme-cover-1.jpg)

---

## 🤔 こんな方におすすめ

### AI エージェントを使っている方
これが**完全体験**です。各エージェント（Hermes、OpenChamber、OpenCode、Pi、Claude…）が作業中に自ら `state_change` を送信し、ピクセルキャラが対応エリアへ移動します。複数エージェントが 1 つの board を共有し、それぞれ自分の名前表示です。

### 実行環境がない方
でもデプロイして使えます：
- `curl` 1 行 / cron / CI ステップで状態送信（HTTP クライアントなら何でも可）
- ページ内蔵の状態コントロールバー（`POST /set_state` 経由）
- ピクセルアート風ステータスページ・リモートワーク board として

---

## 📋 機能

1. **状態可視化** —— 6 状態（`idle` / `writing` / `researching` / `executing` / `syncing` / `error`）をオフィスエリアにマッピング、アニメスプライトと吹き出し付き
2. **昨日のメモ** —— `memory/*.md` の最新日誌を自動読込・サニタイズしてカード表示
3. **マルチエージェント協働** —— join key でゲスト招待、または `POST /events` でセルフ登録、全員の状態をリアルタイム表示
4. **3言語 UI** —— 中/英/日ワンクリック切替、全 UI テキストが即座に更新
5. **カスタムアセット** —— サイドバーでキャラ・シーン・装飾管理、フレーム同期でチラつき防止
6. **AI 部屋デザイン** —— 自前の Gemini API で背景生成、未接続でもコア機能は動作
7. **モバイル対応** —— スマホでサッと状態確認
8. **セキュリティ強化** —— サイドバー保護、本番弱パスワードブロック、Session Cookie 強化
9. **柔軟な公開アクセス** —— Cloudflare Tunnel か、独自ドメイン/リバプロで
10. **デスクトップペットモード** —— 任意の Electron シェルで透明ウィジェット化（下記）
11. **イベント DB** —— 全イベントを保存（SQLite/PostgreSQL）、`GET /events/recent` で読取。board はイベントストリームの純粋なビュー

---

## 🚀 詳細セットアップ

### 1) 依存インストール

```bash
cd star-office-ui-fork
python3 -m pip install -r backend/requirements.txt
```

### 2) データベース選択（任意）

状態ファイルのコピーは不要です：主状態とイベントは DB に保存され、デフォルトはローカル SQLite に自動初期化。PostgreSQL は `DATABASE_URL=postgresql+psycopg2://user:pass@host/star_office` を設定して起動するだけ——マイグレーションは起動時に自動実行されます。

### 3) バックエンド起動

```bash
cd backend
python3 app.py
```

`http://127.0.0.1:19000` を開く

> ✅ ローカル開発はデフォルトで OK。本番では `.env.example` を `.env` にコピーし、`FLASK_SECRET_KEY` と `ASSET_DRAWER_PASS` に強いランダム値を設定してください。
>
> 🏷️ 看板のプレートにオフィス名を出したい場合：`OFFICE_NAME="Wojtek's Office"` を設定して起動。バックエンドはエージェントのワークスペースファイルを読むことを一切やめました。

### 4) 状態切り替え

イベント送信（エージェント向けドア、次セクション参照）：

```bash
curl -X POST http://127.0.0.1:19000/events -H 'Content-Type: application/json' -d '{
  "version": 1, "agent": "hermes", "event_type": "state_change",
  "timestamp": "2026-10-13T14:20:55Z",
  "payload": {"state": "executing", "detail": "テスト実行中"}
}'
```

またはページの状態コントロールバー（`POST /set_state` 経由、主エージェントのみ）。

### 5) 公開アクセス（任意）

```bash
cloudflared tunnel --url http://127.0.0.1:19000
```

`https://xxx.trycloudflare.com` を共有。

### 6) インストール確認（任意）

```bash
python3 scripts/smoke_test.py --base-url http://127.0.0.1:19000
```

全て `OK` ならデプロイ成功。

---

## 🐳 Docker デプロイ

1 つのイメージ、2 つの DB バックエンド、起動時にマイグレーション。CI
（`.github/workflows/docker-image.yml`）がビルドと公開を担当します。Push の前に、
SQLite と実 PostgreSQL の両方でスモークテストを通しています。

```bash
# SQLite（既定）: 初回起動時に backend/sou.db を作成してマイグレーション
docker run -d --name star-office -p 127.0.0.1:19000:19000 \
  ghcr.io/gulasz101/star-office-ui-fork:latest

# PostgreSQL: 同じイメージで、あなたのサーバを指すだけ
docker run -d --name star-office -p 127.0.0.1:19000:19000 \
  -e DATABASE_URL='postgresql+psycopg2://user:pass@host:5432/star_office' \
  ghcr.io/gulasz101/star-office-ui-fork:v1.0.0-agent-agnostic
```

| 環境変数 | 既定値 | 役割 |
|---|---|---|
| `DATABASE_URL` | 未設定 → SQLite（`backend/sou.db`） | `postgresql+psycopg2://…` を指定すると状態層の全体が PostgreSQL に移行します（ADR-034）。 |
| `STAR_BACKEND_PORT` | `19000` | コンテナ内で gunicorn がバインドするポート（`0.0.0.0:$STAR_BACKEND_PORT`）。`-p` の割り当てもお忘れなく。 |
| `FLASK_SECRET_KEY` / `STAR_OFFICE_SECRET` | 開発用デフォルト | セッション署名用。`STAR_OFFICE_ENV=production` では強い値（24 文字以上・非デフォルト）が必須です。 |
| `ASSET_DRAWER_PASS` | `1234` | アセット編集画面の入口。本番ではデフォルト値を使わないでください。 |
| `STAR_OFFICE_ENV` | 未設定 | `production` にすると硬化ガードが有効になり、弱い秘密情報では警告ではなく起動しません。 |
| `OFFICE_NAME` | 未設定 | 看板のテキスト。バックエンドがエージェントのワークスペースファイルを読むことは絶対にありません。 |

コンテナ起動時の流れ:

1. `entrypoint.sh` が `backend/alembic.ini` に対して `alembic upgrade head` を実行します。
   `DATABASE_URL` が優先され、未設定なら SQLite にフォールバックし、`backend/sou.db` が
   なければ作成してマイグレーションします。マイグレーションが失敗したらコンテナは終了します —
   途中のスキーマでサーバを起動することはありません。
2. `exec gunicorn --chdir backend --worker-class gthread --workers 1 --threads 8`
   が `app:app` を配信します。worker を 1 に固定のは意図的です。アプリはプロセス内の状態と
   バックグラウンドタスクを持つため、複数 worker はそれぞれ別の状態を持ってしまいます。
3. イメージの `HEALTHCHECK`（30 秒間隔・3 回リトライ）はダッシュボードと同じ読み取りパス
   `/events/recent?limit=1` を見ます。だからスキーマも接続も壊れているときに、青信号ではなく
   `unhealthy` を正直に出します。

状態の永続化とローカルビルド:

- **PostgreSQL** が永続化の本命です。ボリュームは不要、DB はコンテナの外にあります。
- **SQLite**: イメージ内の `backend/` はデータディレクトリではありません。再起動をまたぐなら
  ディレクトリをマウントして指し示してください — `chown 10001:10001 data` のあと
  `-v "$PWD/data:/data" -e DATABASE_URL='sqlite:////data/sou.db'`。
  `/app/backend` にボリュームを被せるのは絶対にやめてください。そこはアプリのコードです。
- コンテナは非特権の uid **10001** で動きます。
- ローカルビルド: リポジトリ直下で `docker build -t star-office-ui .`、上のイメージ名を
  `star-office-ui` に置き換えれば同じように動きます。
- イメージに秘密情報は入りません。`.dockerignore` が `.git`・`.env*`・仮想環境・キャッシュ・
  すべての `*.db` をビルドコンテキストから除外しており、認証情報は実行時に注入します。

---

## 🤖 Agent 接続（任意のエージェント、1 つのプロトコル）

オフィスには書き込み口が 1 つだけあります：**`POST /events`**。インストールするクライアントも、共有ワークスペースも、必須フレームワークもありません——HTTP POST できるエージェントなら参加できます。

### イベントエンベロープ（Schema v1）

各イベントは小さな JSON エンベロープ（定義：`backend/schemas/event_v1.json`）：

| フィールド | 型 | 説明 |
|-----------|----|------|
| `version` | int | 常に `1` |
| `agent` | string | 表示名（`hermes`、`openchamber`、`opencode`、`pi`、`claude` など）。**未知の名前の 1 件目で自動登録** |
| `event_type` | string | `state_change`、`tool_call`、`file_edit`、`command_run`、`session_start`、`session_end` など——ホワイトリストではなく、未知型はそのまま保存 |
| `timestamp` | string | ISO-8601 時刻（オフセット付きは UTC に正規化） |
| `payload` | object | 型別データ。`state_change` は `{"state": "<6 状態のいずれか>", "detail": "<一言>"}` |

```bash
curl -X POST https://your.office.host/events \
  -H 'Content-Type: application/json' \
  -d '{"version":1,"agent":"claude","event_type":"state_change",
       "timestamp":"2026-10-13T14:20:55Z",
       "payload":{"state":"researching","detail":"コードベース読込中"}}'
```

- `201 {"ok": true}` —— 保存され、次のポーリングで board に表示
- `400 {"ok": false, "error": "<フィールド>: <修正点>"}` —— 機械（hook）が自己修正できるよう問題フィールドを明示

**6 状態 → 3 エリア：**

| 状態 | オフィスエリア | 使いどころ |
|------|---------------|-----------|
| `idle` | 🛋 休憩室（ソファ） | 待機 / タスク完了 |
| `writing` | 💻 作業エリア（デスク） | コード・ドキュメント執筆 |
| `researching` | 💻 作業エリア | 検索 / 調査 |
| `executing` | 💻 作業エリア | コマンド / タスク実行 |
| `syncing` | 💻 作業エリア | データ同期 / push |
| `error` | 🐛 バグコーナー | エラー / デバッグ |

### 主要エージェントの接続点

パターンは常に同じ——状態が変わったら `curl` を 1 発：

| エージェント | 接続ポイント |
|-------------|--------------|
| Hermes | アクションループの開始/終了で `state_change`（`executing` → `idle`） |
| OpenChamber | セッションフックまたは Itsaplan ブリッジ実行時：`session_start` + `state_change` |
| OpenCode | `opencode.json` のカスタムコマンド / プラグインイベント：ツール毎 `tool_call`、稼働/待機 `state_change` |
| Pi | それを駆動するスケジューラの状態遷移点 |
| Claude (Code) | `Stop` / `PostToolUse` フック、または `CLAUDE.md` にルール記載：「タスク開始前に executing、完了後に idle を POST」 |

エージェントが尊重する指示ファイルに 1 回ルールを追加するだけ：

```markdown
## Star Office 状態同期
- タスク開始前：state "executing" の state_change を POST /events に送信
- タスク完了後：state "idle" を送信
外部に出すのは状態語と短い説明だけ——ファイル内容は絶対に送らない。
```

### 読み取りパス

`GET /events/recent` は新しい順にイベント配列を返します（`limit` ≤ 200、`agent`・`event_type`・`since` で絞り込み、下記 API 表参照）。board 自体がこれをポーリングしており、外部ウィジェットも同じドアを使えます。

### ゲストエージェントの招待（join key ルーター）

承認・オンライン TTL・同時接続上限付きの管理された参加には、従来の join 手続きを使います：

**ステップ 1：join key の準備**

バックエンド初回起動時、プロジェクト直下に `join-keys.json` がなければ `join-keys.sample.json`（例：`ocj_example_team_01`）から自動生成。以降は生成された `join-keys.json` を編集して key の追加・変更・削除を。デフォルトは 1 key あたり同時 3 ゲストまで。

**ステップ 2：ゲストが登録して送信する**

```bash
# 1 回 join —— 返り値 {"ok":true,"agentId":"42","authStatus":"approved"}
curl -X POST https://your.office.host/join-agent -H 'Content-Type: application/json' \
  -d '{"name":"AliceBot","joinKey":"ocj_starteam02","state":"idle","detail":"参加しました"}'

# 以降、状態変化時に送信（15 秒間隔が目安）
curl -X POST https://your.office.host/agent-push -H 'Content-Type: application/json' \
  -d '{"agentId":"42","joinKey":"ocj_starteam02","state":"writing","detail":"レポート執筆中"}'
```

ゲストは board に表示され、状態に応じたエリアへ移動。5 分送信がないとオフライン扱い、`POST /leave-agent` で退出します。

**ステップ 3（任意）：オンボーディング資料を渡す**

`frontend/join-office-skill.md` はそのまま貼れる Agent 非依存の手順書です——どんなエージェントでも `curl` だけで実行できます。

---

## 📡 API レファレンス

| エンドポイント | 説明 |
|---------------|------|
| `POST /events` | **エージェントイベント 1 件を投入（上記 v1 エンベロープ）。未知のエージェントは自動登録** |
| `GET /events/recent` | フィード読取：新しい順の `{id, agent, event_type, payload, created_at}` 配列。パラメータ `limit`（≤200）、`agent`（大文字小文字無視）、`event_type`、`since`（ISO） |
| `GET /health` | ヘルスチェック |
| `GET /status` | 主エージェントの状態（`OFFICE_NAME` 設定時は `officeName` 付き） |
| `POST /set_state` | 主エージェントの状態設定（ページ内バー用。`state_change` を 1 件書く） |
| `GET /agents` | 全エージェント一覧（在席/承認状態つき） |
| `POST /join-agent` | ゲストが join key で参加 |
| `POST /agent-push` | ゲストの状態送信（agentId + joinKey） |
| `POST /leave-agent` | ゲスト退出 |
| `GET /yesterday-memo` | 昨日のメモ |
| `GET /config/gemini` | Gemini API 設定取得 |
| `POST /config/gemini` | Gemini API 設定 |
| `GET /assets/generate-rpg-background/poll` | 画像生成状況のポーリング |

---

## 🖥 デスクトップペットモード（任意）

`desktop-pet/` は **Electron** ベースのデスクトップシェル。ピクセルオフィスが透明デスクトップウィジェットになります。

```bash
cd desktop-pet
npm install
npm run dev
```

- 起動時に Python バックエンドを自動起動
- ウィンドウは既定で `http://127.0.0.1:19000/?desktop=1`
- 環境変数でプロジェクトパスと Python パスを設定可能

> ⚠️ **任意の実験的機能**で、主に macOS で開発・テストされています。詳細は [`desktop-pet/README.md`](./desktop-pet/README.md)。
>
> 🙏 デスクトップペットは [@Zhaohan-Wang](https://github.com/Zhaohan-Wang) による独立貢献——感謝します！

---

## 🎨 アセットとライセンス

### アセット 귀属

ゲストキャラアニメは **LimeZu** の無料アセットを使用：
- [Animated Mini Characters 2 (Platformer) [FREE]](https://limezu.itch.io/animated-mini-characters-2-platform-free)

再配布・デモ時は出典を明記し、元のライセンスに従ってください。

### ライセンス

- **コード / ロジック：MIT**（[`LICENSE`](./LICENSE) 参照）
- **アートアセット：非商用のみ**（学習 / デモ / 共有）

> 商用利用の場合は全アートアセットを自作に置き換えてください（本分支は Apache-2.0 モデルでアセットパック全面再生成中——プロジェクト board 参照）。

---

## 📝 更新履歴

| 日付 | 概要 | 詳細 |
|------|------|------|
| 2026-10 | 🌱 **分支プログラム** —— Phase 1–4：イベント DB（SQLite/Postgres）、`POST /events` + `GET /events/recent`、OpenClaw デカップリング完了（`SKILL.md`・`set_state.py`・`office-agent-push.py` 削除、接続方法は本ドキュメントが正本） | Issue SOU-18 … SOU-46 |
| 2026-03-06 | 🔌 デフォルトポート変更 —— ローカル browser-control サービスとの競合回避のため 18791 → 19000。スクリプト・デスクトップシェル・ドキュメントも追随 | [`docs/CHANGELOG_2026-03.md`](./docs/CHANGELOG_2026-03.md) |
| 2026-03-05 | 📱 安定性修正 —— CDN キャッシュ、非同期画像生成、モバイルサイドバー、join key 有効期限と同時接続 | [`docs/UPDATE_REPORT_2026-03-05.md`](./docs/UPDATE_REPORT_2026-03-05.md) |
| 2026-03-04 | 🔒 P0/P1 セキュリティ強化 —— 弱パスワードブロック、バックエンドリファクタ、状態タイムアウト自動 idle、スケルトンロード | [`docs/UPDATE_REPORT_2026-03-04_P0_P1.md`](./docs/UPDATE_REPORT_2026-03-04_P0_P1.md) |
| 2026-03-03 | 📋 オープンソース公開チェックリスト完了 | [`docs/OPEN_SOURCE_RELEASE_CHECKLIST.md`](./docs/OPEN_SOURCE_RELEASE_CHECKLIST.md) |
| 2026-03-01 | 🎉 **v2 リビルド** —— 3言語対応、アセット管理、AI 部屋デザイン、アセット全面更新 | [`docs/FEATURES_NEW_2026-03-01.md`](./docs/FEATURES_NEW_2026-03-01.md) |

---

## 📁 プロジェクト構成

```text
star-office-ui-fork/
├── backend/            # Flask バックエンド
│   ├── app.py          # ルート：/events、/events/recent、/status、/agents…
│   ├── db.py           # SQLite/Postgres イベントアダプタ（Phase 1）
│   ├── schemas/event_v1.json  # POST /events の契約
│   ├── migrations/     # Alembic マイグレーション
│   ├── requirements.txt
│   └── run.sh
├── frontend/           # フロントページ & アセット
│   ├── index.html
│   ├── join.html
│   ├── invite.html
│   ├── join-office-skill.md   # ゲスト用手順書（Agent 非依存）
│   └── layout.js
├── desktop-pet/        # Electron デスクトップシェル（任意）
├── docs/               # ドキュメント & スクリーンショット
│   └── screenshots/
├── scripts/            # スモークテスト、セキュリティチェック、開発支援
├── state.sample.json     # 旧状態テンプレ（状態は DB 保存）
├── join-keys.sample.json # join key テンプレ（実行時に join-keys.json 生成）
└── LICENSE             # MIT ライセンス
```

---

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/image?repos=ringhyacinth/Star-Office-UI&type=date&legend=top-left)](https://www.star-history.com/?repos=ringhyacinth%2FStar-Office-UI&type=date&legend=top-left)
