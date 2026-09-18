# Connecting MCP clients

This page shows how to register copyeditor in each client and complete the login. `MCP_URL`
below is either the local `none` server (`http://127.0.0.1:8080/mcp`, no login) or your Google
authenticated deployment (`https://<your-host>/mcp`, browser login with an allowed account). Never
enter the Google OAuth client secret in any client: the server registers each client dynamically.

Replace the literal `MCP_URL` in commands with your endpoint. Local URLs work for Claude Code and
Codex running on the same machine. The Claude Desktop remote connector and ChatGPT connect from
the cloud: use the HTTPS Google-authenticated deployment for those clients, not localhost.
Before Google login, complete the [callback log checks](../README.md#retention-and-operational-logs).

After registering, look for the two tools `polish_text` and `lint_text`, then try `polish_text`
once with a short text that is not confidential. Tool calls still require the client's approval.
A sentence that already follows the rules comes back unchanged; to see a correction, try
`一覧画面から申請の状況を確認することが可能です。`.

## Claude Code (CLI)

```sh
claude mcp add --transport http copyeditor MCP_URL
```

Add `--scope user` to make the server available in every project. Start `claude`, run `/mcp`,
select `copyeditor` and complete the browser login when the server requires it. `claude mcp list`
shows the connection state.

Flags verified with Claude Code 2.1.276 and the [official MCP guide](https://code.claude.com/docs/en/mcp).

## Claude Desktop

Custom remote connectors support Free (one custom connector), Pro, Max, Team and Enterprise.
Organization controls apply. The [current guide](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities)
uses **Customize → Connectors → + → Add custom connector**. The following **Settings** route
worked in the owner's Desktop test on 2026-09-18; its app build was not recorded. Japanese UI
labels below are translations, not a separately verified localized interface.

1. Open **Settings** → **Connectors** → **Add custom connector**.
2. Name `copyeditor`, remote MCP server URL `MCP_URL`. Leave the OAuth client ID and secret fields
   empty and click **Add**.
3. Click **Connect** on the new connector. A browser opens; sign in with an allowed Google account
   and approve. Return to the app when it reports the connection.
4. In a new chat, open the tools menu (**+**), choose **Connectors**, enable `copyeditor` and
   confirm that `polish_text` and `lint_text` are listed.

## Codex CLI

```sh
codex mcp add copyeditor --url MCP_URL
codex mcp login copyeditor --oauth-client-registration dcr
```

The second command runs the browser login for a Google authenticated server (skip it for the local
`none` server). Start `codex`, run `/mcp` and confirm that `copyeditor` is connected.

These flags were checked with `codex-cli 0.154.0` help. See the
[official MCP guide](https://developers.openai.com/codex/mcp); `dcr` explicitly selects dynamic registration.

## Codex app (desktop)

This route and restart workaround were verified by the owner in the migration-source PoC;
the desktop build number was not recorded. Other builds may expose different controls.

1. Open **Plugins** → **MCPs** and register `copyeditor` with `MCP_URL`.
2. For a Google authenticated server, log in from a terminal with the same name:

```sh
codex mcp login copyeditor --oauth-client-registration dcr
```

3. Quit the app completely and start it again; enabling the entry in the list alone did not expose
   the tools in a conversation. The app version verified had no Restart or Authenticate button.

## ChatGPT desktop app

Desktop setup and copyeditor tool calls are **not verified**. Use ChatGPT in a browser for the
documented developer-mode setup. As checked on 2026-09-18, the
[developer guide](https://developers.openai.com/api/docs/guides/developer-mode) lists Plus, Pro,
Business, Enterprise and Education on web. The [workspace help page](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
describes Business/Enterprise/Edu full MCP access and limits Pro to read/fetch. Availability and
menus differ between these official pages; do not assume your account supports both copyeditor tools.

1. On web, follow the developer guide: **Settings → Security and login → Developer mode**, then
   **Plugins → +** to create an app. For the workspace UI documented in Help, use
   **Settings → Apps → Advanced Settings**, then **Apps → Create**. Business requires an
   admin/owner; Enterprise/Edu may grant developer access through workspace permissions.
2. Name it `copyeditor`, use the HTTPS `MCP_URL`, select **OAuth**, and leave static client
   credentials empty. Select **DCR** if registration strategy is offered. Complete Google login
   and tool discovery, then create the app.
3. Select the app in a web conversation's developer-mode/tools menu and verify both tools and
   a small test call. Native desktop availability and localized UI labels remain unverified;
   if unavailable, continue on web or use Codex CLI.

## Troubleshooting

- The custom connector option is missing: check the client version, plan and organization settings. Use Claude
  Code or Codex CLI instead.
- Google blocks a test login: check the consent screen's audience and test-user list with the
  server owner; an unverified-app warning alone does not establish the cause.
- "Access denied" after login: the account is not in the allowed domains or e-mails. Check which
  Google account the browser used.
- The tools disappear after the server restarts or is redeployed: OAuth state is kept in memory.
  Reconnect and log in again.

## 日本語

各クライアントに copyeditor を登録してログインを完了する手順です。以下の `MCP_URL` は、ローカルの
`none` サーバー（`http://127.0.0.1:8080/mcp`、ログインなし）か、Google 認証付きの配備先
（`https://<your-host>/mcp`、許可されたアカウントでブラウザ ログイン）のどちらかです。Google
OAuth のクライアント シークレットはどのクライアントにも入力しません。サーバーがクライアントを
動的に登録します。

コマンド内の `MCP_URL` は接続先に置き換えます。ローカル URL は同じ端末の Claude Code / Codex 用です。
Claude Desktop のリモートコネクタと ChatGPT はクラウドから接続するため、localhost ではなく
Google 認証付きの HTTPS 配備先を使います。Google ログイン前に[callback ログの確認](../README.md#保持範囲と運用ログ)を済ませてください。

登録後は `polish_text` と `lint_text` の 2 つのツールが見えることを確認し、機密でない短い文で
`polish_text` を 1 回試します。ツール呼び出しにはクライアント側の承認が必要です。ルールに沿った文は
そのまま返ります。修正を見たいときは `一覧画面から申請の状況を確認することが可能です。` を送って
ください。

### Claude Code（CLI）

```sh
claude mcp add --transport http copyeditor MCP_URL
```

すべてのプロジェクトで使うなら `--scope user` を付けます。`claude` を起動して `/mcp` を実行し、
`copyeditor` を選んで、サーバーが要求する場合はブラウザ ログインを完了します。`claude mcp list`
で接続状態を確認できます。

フラグは Claude Code 2.1.276 と[公式 MCP ガイド](https://code.claude.com/docs/en/mcp)で確認しました。

### Claude Desktop

リモートのカスタムコネクタは Free（1 個まで）/ Pro / Max / Team / Enterprise に対応し、組織の制限が適用されます。
[現行ガイド](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities)は
**Customize → Connectors → + → Add custom connector** です。以下の **設定** 経由は所有者が
2026-09-18 に Desktop で確認した手順ですが、build 番号は未記録です。日本語の画面名は訳であり、
日本語 UI を別途実機確認したものではありません。

1. **設定** → **コネクタ** → **カスタム コネクタを追加** を開く
2. 名前 `copyeditor`、リモート MCP サーバーの URL に `MCP_URL`。OAuth クライアント ID とシークレットの
   欄は空のまま **追加**
3. 追加したコネクタの **接続** を押す。ブラウザが開くので、許可された Google アカウントでログインして
   承認する。接続完了の表示が出たらアプリに戻る
4. 新しいチャットでツール メニュー（**＋**）→ **コネクタ** から `copyeditor` を有効にし、`polish_text`
   と `lint_text` が表示されることを確認する

### Codex CLI

```sh
codex mcp add copyeditor --url MCP_URL
codex mcp login copyeditor --oauth-client-registration dcr
```

2 行目は Google 認証付きサーバーのブラウザ ログインです（ローカルの `none` サーバーでは不要）。
`codex` を起動して `/mcp` で `copyeditor` が接続済みであることを確認します。

フラグは `codex-cli 0.154.0` の help で確認しました。[公式 MCP ガイド](https://developers.openai.com/codex/mcp)も参照してください。
`dcr` は動的クライアント登録を明示的に選びます。

### Codex アプリ（デスクトップ）

この経路と再起動の対処は所有者が移植元の PoC で確認したものです。Desktop の build 番号は
未記録で、別 build では操作項目が異なる場合があります。

1. **Plugins** → **MCPs** を開き、`copyeditor` を `MCP_URL` で登録する
2. Google 認証付きサーバーの場合は、ターミナルで同じ名前でログインする

```sh
codex mcp login copyeditor --oauth-client-registration dcr
```

3. アプリを完全に終了して起動し直す。一覧で有効にしただけでは会話にツールが出ませんでした。確認した
   バージョンには Restart / Authenticate ボタンはありません

### ChatGPT デスクトップ アプリ

Desktop での設定と copyeditor 呼び出しは **未検証** です。公式に案内されている開発者モードの設定は
ブラウザで行います。2026-09-18 の[開発者ガイド](https://developers.openai.com/api/docs/guides/developer-mode)は
Web の Plus / Pro / Business / Enterprise / Education を対象としています。一方、
[ワークスペース向けヘルプ](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)は
Business / Enterprise / Edu の full MCP と Pro の read/fetch 制限を記載しています。公式資料間で
対象と画面名が異なるため、自分のアカウントで両ツールが使えるとは決めつけず確認してください。

1. Web で開発者ガイドの **Settings → Security and login → Developer mode** を有効にし、
   **Plugins → +** から作成します。ヘルプに記載されたワークスペース UI では
   **Settings → Apps → Advanced Settings**、次に **Apps → Create** です。Business は管理者・所有者、
   Enterprise / Edu はワークスペース権限で許可された開発者が利用します。
2. 名前 `copyeditor`、HTTPS の `MCP_URL`、認証 **OAuth** とし、静的なクライアント認証情報は空にします。
   登録方式が選べる場合は **DCR** を選び、Google ログインとツール検出を完了して作成します。
3. Web の会話の開発者モード・ツールメニューでアプリを選び、両ツールと短文での呼び出しを確認します。
   ネイティブ Desktop の対応と日本語 UI 表記は未検証です。使えない場合は Web または Codex CLI を使ってください。

### つまずいたとき

- カスタム コネクタの項目が無い: クライアントの版・プラン・組織設定を確認してください。Claude Code か Codex CLI を
  使ってください
- Google がテストログインを拒否する: 同意画面の対象とテストユーザー一覧をサーバー所有者に確認してください。
  未確認アプリの警告だけでは原因を特定できません
- ログイン後に「アクセスが拒否されました」: 許可ドメイン / メールに含まれないアカウントです。ブラウザが
  使った Google アカウントを確認してください
- サーバーの再起動や再デプロイの後にツールが消える: OAuth の状態はメモリ上だけです。再接続して
  ログインし直します
