#!/usr/bin/env bash
#
# フロントエンドを Azure Static Web Apps へ配信する。
#
# リポジトリの frontend/index.html は `<meta name="api-base" content="">` を
# 空のままにしてある。ここを埋めてしまうと、ローカル開発（npm run serve）でも
# 本番APIを見に行ってしまうため。
# そこで配信用のコピーを作り、そのコピーにだけ Function App のURLを差し込む。
#
# 使い方:
#   ./scripts/deploy_frontend.sh
#   SWA_NAME=... FUNCAPP_NAME=... ./scripts/deploy_frontend.sh   # 別環境へ出す場合
#
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-bousai-map}"
SWA_NAME="${SWA_NAME:-swa-bousai-map-636d22}"
FUNCAPP_NAME="${FUNCAPP_NAME:-func-bousai-map-636d22}"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$(mktemp -d)"
# 失敗しても一時ディレクトリを残さない
trap 'rm -rf "$STAGING"' EXIT

API_BASE="https://${FUNCAPP_NAME}.azurewebsites.net"

echo "==> 配信用コピーを作成"
cp -r "$PROJECT_ROOT/frontend/." "$STAGING/"
# テストは配信しない（公開する必要がなく、転送量も無駄）
rm -rf "$STAGING/tests"

echo "==> API の基底URLを差し込み: $API_BASE"
python3 - "$STAGING/index.html" "$API_BASE" <<'PYEOF'
import sys

path, api_base = sys.argv[1], sys.argv[2]
placeholder = '<meta name="api-base" content="" />'

with open(path, encoding="utf-8") as fp:
    html = fp.read()

if placeholder not in html:
    # 差し込みに失敗したまま配信すると、全APIが同一オリジンに向いて404になる。
    # 気付きにくいので、ここで止める。
    sys.exit(f"エラー: {placeholder} が index.html に見つかりません")

with open(path, "w", encoding="utf-8") as fp:
    fp.write(html.replace(placeholder, f'<meta name="api-base" content="{api_base}" />'))
PYEOF

echo "==> デプロイトークンを取得"
TOKEN="$(az staticwebapp secrets list \
  --name "$SWA_NAME" --resource-group "$RESOURCE_GROUP" \
  --query "properties.apiKey" -o tsv)"

if [ -z "$TOKEN" ]; then
  echo "エラー: デプロイトークンを取得できませんでした（az login 済みか確認してください）" >&2
  exit 1
fi

echo "==> Static Web Apps へ配信"
# トークンが標準出力に混ざらないよう、grep で落としてから表示する
npx --yes @azure/static-web-apps-cli@latest deploy "$STAGING" \
  --deployment-token "$TOKEN" \
  --env production 2>&1 | grep -viE "token|secret"

SWA_HOST="$(az staticwebapp show \
  --name "$SWA_NAME" --resource-group "$RESOURCE_GROUP" \
  --query defaultHostname -o tsv)"

echo
echo "完了: https://${SWA_HOST}"
