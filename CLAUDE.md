# CLAUDE.md

## 1. 開発・テスト用コマンド (Build & Test Commands)
*   仮想環境の有効化: `source .venv/bin/activate`
*   依存関係のインストール: `pip install -r requirements.txt`
*   バックエンドテスト実行 (pytest): `pytest` または `python -m pytest`
*   フロントエンドテスト実行: `npm run test`
*   ローカル開発サーバー起動: `func start` (Azure Functions Core Tools)

## 2. 技術スタック・環境 (Technology Stack)
*   **環境:** Linux (/var/www) 上での開発、Azure環境へのデプロイ
*   **バックエンド:** Python 3.10+ (Azure Functions V2 Programming Model)
*   **フロントエンド:** Vanilla JS または React + Tailwind CSS, Azure Maps Web SDK
*   **テスト:** pytest (Backend), Vitest / Jest (Frontend)

## 3. 重要なコーディング規約 (Coding Constraints)
*   **コード提示の厳守ルール:** ソースコードの変更や提示を行う際は、一部の抜粋ではなく、必ず過去のソースを鑑みた**「該当ファイルのフルコード（全コード）」**を提供すること。
*   **セキュリティ:** AzureのAPIキーや接続文字列などの資格情報は、絶対にソースコード内にハードコードせず、必ず `os.environ` などの環境変数から読み込むこと。
*   **エラーハンドリング:** 外部API呼び出し（`requests` 等）やファイル入出力には必ず `try-except` を用い、適切な例外処理とロギングを行うこと。
*   **テストファースト:** 新機能の実装時は、実装コードよりも先にテストコード（`tests/`）を作成すること。