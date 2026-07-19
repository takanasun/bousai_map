/**
 * 地価データの表示・フィルタロジック。
 *
 * 国土交通省の地価公示（地点ごとの円/㎡）を防災施設と重ねて、
 * 「地価が安く避難所が近いエリア」を探せるようにする。
 *
 * 注意: 地価公示は「地点」の価格であり、町丁目全体の相場ではない。
 * 同じ町でも場所により差がある点は画面にも明記する。
 */

/** サーバが実データ範囲を返せなかった場合の既定範囲（円/㎡）。 */
export const DEFAULT_PRICE_RANGE = { min: 150000, max: 500000 };

/**
 * 色分けの既定の区切り（円/㎡）。
 *
 * 神奈川県全域の地価は 1,170〜15,600,000円（13,000倍）で、中央値は190,000円。
 * 最小〜最大を線形に分けると **99.8%が最下段に潰れて全部同じ色**になるため、
 * 実データのパーセンタイルに合わせた区切りを既定にする。
 *   10% 87,500 / 25% 135,000 / 50% 190,000 / 75% 281,000 / 90% 388,000
 */
export const PRICE_BREAKS = [80000, 140000, 200000, 300000, 500000];

/** 地価の色分け（安い=青 → 高い=赤）。人口密度の緑〜赤と区別する。 */
export const PRICE_COLORS = [
  { r: 33, g: 102, b: 172 },   // 濃い青（安い）
  { r: 103, g: 169, b: 207 },  // 水色
  { r: 247, g: 247, b: 247 },  // 灰
  { r: 239, g: 138, b: 98 },   // 橙
  { r: 178, g: 24, b: 43 },    // 濃い赤（高い）
];

/** 円/㎡ を「15万円」のように整形する。 */
export function formatPrice(yenPerSqm) {
  const value = Number(yenPerSqm);
  if (!Number.isFinite(value)) return '0万円';
  return `${Math.round(value / 10000).toLocaleString()}万円`;
}

/**
 * スライダーの範囲を決める。
 * サーバの実データ範囲を優先し、壊れていれば既定値に落とす。
 */
export function resolvePriceRange(range) {
  const min = Number(range && range.min);
  const max = Number(range && range.max);
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { ...DEFAULT_PRICE_RANGE };
  if (min <= 0 || max <= 0 || min >= max) return { ...DEFAULT_PRICE_RANGE };
  return { min, max };
}

/** 地価アイコンのID接頭辞。他レイヤーのアイコンと衝突しないよう分ける。 */
export const PRICE_ICON_PREFIX = 'landprice-square-';

/** 価格が何段目に入るかを返す（0 = 最も安い）。 */
export function priceBandIndex(price, breaks = PRICE_BREAKS) {
  const value = Number(price);
  if (!Number.isFinite(value)) return 0;
  let index = 0;
  for (let i = 0; i < breaks.length; i += 1) {
    if (value >= breaks[i]) index = i;
  }
  return Math.min(index, PRICE_COLORS.length - 1);
}

/** アイコンの一辺（px）。地図上で施設ピンより控えめに見える大きさ。 */
const ICON_SIZE = 20;

/**
 * 価格帯の色で塗った正方形アイコンを data URI で返す。
 *
 * Azure Maps の `createFromTemplate` はテンプレート名の解決に依存し、
 * 失敗すると地図の初期化ごと落ちる。自前の SVG なら SDK の事情に
 * 左右されず、色も自由に決められる。
 *
 * @param {{r:number,g:number,b:number}} color
 */
export function squareIconDataUri(color) {
  const c = color || { r: 0, g: 0, b: 0 };
  const size = ICON_SIZE;
  const inset = 2; // 縁の分だけ内側に描く
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">` +
    `<rect x="${inset}" y="${inset}" ` +
    `width="${size - inset * 2}" height="${size - inset * 2}" ` +
    `fill="rgb(${c.r},${c.g},${c.b})" stroke="#ffffff" stroke-width="2"/>` +
    `</svg>`;
  // '#' や '<' を生で残すと data URI として壊れるためエンコードする
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** 価格帯ごとのアイコンID一覧。 */
export function priceIconIds() {
  return PRICE_COLORS.map((_, index) => `${PRICE_ICON_PREFIX}${index}`);
}

/**
 * 価格帯に応じてアイコンを出し分ける式。
 *
 * 色は画像そのものに焼き込むため、`priceBand` プロパティで画像を選ぶ。
 * （SymbolLayer の iconOptions.image は色を式で変えられない）
 */
export function buildLandPriceIconExpression() {
  const ids = priceIconIds();
  const expression = ['match', ['get', 'priceBand']];
  ids.forEach((id, index) => {
    expression.push(index, id);
  });
  expression.push(ids[0]); // 既定（priceBand が無い場合）
  return expression;
}

/** 上限(円/㎡)以下の地点だけ返す。上限が null なら全件。 */
export function filterLandsByMaxPrice(lands, maxPrice) {
  if (!Array.isArray(lands)) return [];
  if (maxPrice === null || maxPrice === undefined) return [...lands];
  return lands.filter((l) => Number(l && l.pricePerSqm) <= maxPrice);
}

/** 平均地価が上限以下のエリアだけ返す。 */
export function filterAreasByMaxPrice(areas, maxPrice) {
  if (!Array.isArray(areas)) return [];
  if (maxPrice === null || maxPrice === undefined) return [...areas];
  return areas.filter((a) => Number(a && a.avgPricePerSqm) <= maxPrice);
}

/**
 * 地価地点を GeoJSON に変換する。
 *
 * @param {Array} lands
 * @param {number[]} [breaks] 価格帯の区切り。アイコン選択用の priceBand を付ける。
 */
export function landsToFeatureCollection(lands, breaks = PRICE_BREAKS) {
  if (!Array.isArray(lands)) return { type: 'FeatureCollection', features: [] };

  const features = [];
  for (const land of lands) {
    const loc = land && land.location;
    if (!loc) continue;
    const lat = Number(loc.lat);
    const lng = Number(loc.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;

    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lng, lat] },
      properties: {
        id: land.id,
        town: land.town || '',
        address: land.address || '',
        pricePerSqm: Number(land.pricePerSqm) || 0,
        // アイコンの出し分けに使う（色は画像に焼き込むため式では変えられない）
        priceBand: priceBandIndex(land.pricePerSqm, breaks),
        uses: Array.isArray(land.uses) ? land.uses : [],
        year: land.year || '',
      },
    });
  }

  return { type: 'FeatureCollection', features };
}

/**
 * 実データの分布から色の区切りを作る。
 *
 * 最小〜最大の線形分割ではなくパーセンタイルを使う。地価は一部の商業地が
 * 極端に高く、線形だとほぼ全地点が最下段に潰れて色が意味をなさなくなる。
 * 上端は95パーセンタイルで止め、それ以上は同じ色（最も濃い赤）に丸める。
 *
 * @param {number[]} prices 実データの価格一覧
 * @returns {number[]} 昇順・重複なしの区切り
 */
export function buildPriceBreaks(prices) {
  if (!Array.isArray(prices) || prices.length === 0) return [...PRICE_BREAKS];

  const sorted = prices
    .map(Number)
    .filter((p) => Number.isFinite(p) && p > 0)
    .sort((a, b) => a - b);
  if (sorted.length === 0) return [...PRICE_BREAKS];

  // 下から上へ等間隔のパーセンタイル。上端は95%で止めて外れ値を切る
  const quantiles = [0.05, 0.3, 0.55, 0.8, 0.95];
  const breaks = quantiles.map(
    (q) => sorted[Math.min(Math.floor(sorted.length * q), sorted.length - 1)],
  );

  // interpolate は停止点が昇順かつ重複なしである必要がある。
  // 同じ価格ばかりのデータでも壊れないよう、重複したら1円ずつ持ち上げる。
  for (let i = 1; i < breaks.length; i += 1) {
    if (breaks[i] <= breaks[i - 1]) breaks[i] = breaks[i - 1] + 1;
  }
  return breaks;
}

/**
 * 地価に応じた色の式を組み立てる。
 *
 * @param {number[]} [prices] 実データの価格一覧。省略時は既定の区切りを使う。
 */
export function buildLandPriceColorExpression(prices) {
  const breaks = buildPriceBreaks(prices);

  const expression = ['interpolate', ['linear'], ['get', 'pricePerSqm']];
  PRICE_COLORS.forEach((color, index) => {
    expression.push(breaks[index], `rgb(${color.r}, ${color.g}, ${color.b})`);
  });
  return expression;
}
