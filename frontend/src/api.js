/**
 * バックエンド (Azure Functions) との通信層。
 *
 * 仕様 7.1 に従い、失敗しても画面を真っ白にせず、利用者に見せられる
 * 日本語メッセージを持つ ApiError に正規化して投げる。
 */

/** API 呼び出しの失敗を表す例外。HTTP ステータスを保持する。 */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** ローカルの静的配信サーバーとして使われがちなポート。 */
const STATIC_DEV_PORTS = ['3000', '5173', '5500', '8000', '8080'];

/** ローカルで `func start` が待ち受けるオリジン。 */
const FUNCTIONS_DEV_ORIGIN = 'http://localhost:7071';

/**
 * デプロイ時に差し込まれる前のプレースホルダ。
 * 差し替え忘れをそのままURLに使うと全APIが404になるため検出して無視する。
 */
const API_BASE_PLACEHOLDER = '__API_BASE__';

/**
 * API の基底 URL を決める。
 *
 * 優先順位:
 *   1. `<meta name="api-base">` の明示指定
 *      本番は静的配信(SWA)とAPI(Function App)がオリジンの異なる別リソース
 *      になるため、ここでAPI側を指す。
 *   2. ローカルの静的サーバー(5173等)から開いた場合は `func start` のポート
 *   3. それ以外は同一オリジン
 *
 * @param {{port: string, hostname: string}|null} location window.location 相当
 * @param {string} [configuredBase] meta タグで指定された基底 URL
 * @returns {string} 基底 URL（同一オリジンなら空文字）
 */
export function resolveApiBase(location, configuredBase) {
  const configured = (configuredBase || '').trim();
  if (configured && configured !== API_BASE_PLACEHOLDER) {
    // 連結時に `//api/...` にならないよう末尾のスラッシュを落とす
    return configured.replace(/\/+$/, '');
  }

  if (!location) return '';
  if (STATIC_DEV_PORTS.includes(location.port)) return FUNCTIONS_DEV_ORIGIN;
  return '';
}

/** `<meta name="api-base" content="...">` を読む。無ければ空文字。 */
function readConfiguredApiBase() {
  if (typeof document === 'undefined') return '';
  const meta = document.querySelector('meta[name="api-base"]');
  return meta ? meta.getAttribute('content') || '' : '';
}

const API_BASE =
  typeof window !== 'undefined' && window.location
    ? resolveApiBase(window.location, readConfiguredApiBase())
    : '';

/**
 * JSON を取得する共通処理。
 * @param {string} path `/api/...`
 * @param {string} fallbackMessage サーバがメッセージを返さなかった場合の文言
 * @param {object} [init] fetch のオプション（POST 用）
 */
async function fetchJson(path, fallbackMessage, init) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch {
    // ネットワーク断・CORS 失敗など
    throw new ApiError('サーバーに接続できませんでした', 0);
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const message = (body && body.error) || fallbackMessage;
    throw new ApiError(message, response.status);
  }

  return body || {};
}

/** Azure Maps のキーなどフロント用設定を取得する。 */
export async function fetchMapConfig() {
  return fetchJson('/api/config', '地図の設定を取得できませんでした');
}

/**
 * 人口密度メッシュを取得する。
 *
 * 解像度は元データ（メッシュコードの桁数）で決まるため、選べる一覧は
 * サーバが持つデータ次第。応答に含まれる availableResolutions をそのまま
 * UI の選択肢に使う（存在しない解像度を画面に出さないため）。
 *
 * @param {string} [resolution] "1km" / "500m" / "250m" / "125m"。省略時はサーバの既定。
 * @returns {Promise<{items: Array, resolution: string, availableResolutions: string[]}>}
 */
export async function fetchMesh(resolution) {
  const path = resolution ? `/api/mesh?resolution=${encodeURIComponent(resolution)}` : '/api/mesh';
  const body = await fetchJson(path, '人口メッシュのデータが取得できませんでした');
  return {
    items: Array.isArray(body.items) ? body.items : [],
    resolution: body.resolution || '',
    availableResolutions: Array.isArray(body.availableResolutions)
      ? body.availableResolutions
      : [],
  };
}

/** 公衆トイレを取得する。 */
export async function fetchToilets() {
  const body = await fetchJson('/api/toilets', 'トイレのデータが取得できませんでした');
  return Array.isArray(body.items) ? body.items : [];
}

/** 避難所を取得する。 */
export async function fetchEvacuationSites() {
  const body = await fetchJson('/api/evacuation', '避難所のデータが取得できませんでした');
  return Array.isArray(body.items) ? body.items : [];
}

/** 医療機関を取得する。 */
export async function fetchHospitals() {
  const body = await fetchJson('/api/hospitals', '医療機関のデータが取得できませんでした');
  return Array.isArray(body.items) ? body.items : [];
}

/**
 * 地価データを取得する。
 *
 * @returns {Promise<{items:Array, areas:Array, priceRange:{min:number,max:number}}>}
 */
export async function fetchLandPrice() {
  const body = await fetchJson('/api/landprice', '地価データが取得できませんでした');
  return {
    items: Array.isArray(body.items) ? body.items : [],
    areas: Array.isArray(body.areas) ? body.areas : [],
    priceRange: body.priceRange || null,
  };
}

/**
 * 住所を緯度経度に変換する。
 *
 * サーバ側の GEOCODER_BACKEND が mock のままだと数件しか解決できない。
 * 実住所を引くには azure_maps を選ぶこと。
 *
 * @param {string} address
 * @returns {Promise<{lat:number,lng:number}>}
 */
export async function geocodeAddress(address) {
  const path = `/api/geocode?address=${encodeURIComponent(address)}`;
  const body = await fetchJson(path, '住所を検索できませんでした');
  return body.location;
}

/**
 * AI防災アシスタントに質問する。
 *
 * 周辺データの収集とプロンプト組み立てはサーバ側で行う。
 * ブラウザからは質問文と基準座標だけを送る（LLMのキーは一切渡らない）。
 *
 * @param {string} question
 * @param {{lat: number, lng: number}} origin 基準地点（自宅または現在地）
 * @returns {Promise<{answer: string, sources: object}>}
 */
export async function postChat(question, origin) {
  const body = await fetchJson('/api/chat', 'AIの応答を取得できませんでした', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, lat: origin.lat, lng: origin.lng }),
  });
  return {
    answer: body.answer || '',
    sources: body.sources || {},
    // AIに渡した施設。回答文に名前が出たものを地図上で強調する
    candidates: Array.isArray(body.candidates) ? body.candidates : [],
  };
}
