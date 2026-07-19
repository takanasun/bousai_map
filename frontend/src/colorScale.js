/**
 * 人口密度 → 色 / 不透明度 の変換。
 *
 * 仕様 5.2:「人口が多いマス＝赤（不透明度高） 〜 人口が少ないマス＝緑（不透明度低）」
 *
 * 値はセル内の人口（人）であり、セルが細かいほど値域が下がる
 * （面積が1/4になれば人口もおおむね1/4）。区切りを固定すると細かい解像度で
 * 全部緑になってしまうため、**解像度ごとに区切りを切り替える**。
 *
 * 2通りの使い方を提供する:
 *   1. densityToColor / densityToOpacity … JS 側で値を求める（凡例やテスト用）
 *   2. buildFillColorExpression …… Azure Maps のデータ駆動スタイル式を返す
 *      （数万件のポリゴンを個別に色計算せず、GPU側で解決させるため。
 *        仕様 7.1「DOM要素を直接増やさずデータレイヤーを使う」に対応）
 */

/** 区切りに対応する色（緑 → 黄 → 赤）。全解像度で共通。 */
export const DENSITY_COLORS = [
  { r: 26, g: 152, b: 80 },    // 緑
  { r: 145, g: 207, b: 96 },   // 黄緑
  { r: 254, g: 224, b: 139 },  // 黄
  { r: 253, g: 174, b: 97 },   // 橙
  { r: 227, g: 74, b: 51 },    // 赤
  { r: 179, g: 0, b: 0 },      // 濃赤
];
// 注: 色は「赤みの強さ (r - g)」が単調増加するよう選んである。
// 差し替える際は colorScale.test.js の単調性テストが守ってくれる。

/** 配色の識別子。 */
export const PALETTE_NORMAL = 'normal';
export const PALETTE_CUD = 'cud';

/**
 * カラーユニバーサルデザイン（CUD）配色。東京メトロの路線カラーをそのまま使う。
 *
 * 銀座線G / 丸ノ内線M / 日比谷線H / 東西線T / 千代田線C / 半蔵門線Z の6色。
 * 路線カラーは9路線を区別するために互いを最大限離した配色なので、
 * 隣り合う段の見分けは非常に良い。地図背景と不透明度0.85で合成した実測:
 *
 *   配色                    正常   P型   D型
 *   既定(緑→黄→赤)           9.8   3.6   7.0
 *   cividis                 12.9  13.0  13.1
 *   銀座線→半蔵門線(再構成)   28.8  29.2  25.8
 *   これ(路線カラーそのまま)   38.9  27.8  37.7
 *
 * 【トレードオフ】明度が単調でない（L* = 74, 57, 77, 64, 71, 62）。
 * つまり色を見て「どちらが混雑しているか」は判断できず、凡例との
 * 照合が必要になる。カテゴリ配色を順序尺度に使う以上これは避けられない。
 * ひと目で危険度を読ませたい場合は、明度が単調な配色に戻すこと
 * （git履歴に cividis 版と銀座線→半蔵門線の再構成版がある）。
 *
 * なお東京メトロ自身の色覚対応は、色を変えることではなく路線記号
 * (G/M/H/T/C/Y/Z/N/F)を足すことだった。色だけに情報を載せない、という
 * 原則はこのアプリでも採っている（医療機関=円＋白十字、地価=正方形）。
 */
export const DENSITY_COLORS_CUD = [
  { r: 255, g: 149, b: 0 },    // G 銀座線オレンジ（人口が少ない）
  { r: 246, g: 46, b: 54 },    // M 丸ノ内線レッド
  { r: 181, g: 181, b: 172 },  // H 日比谷線シルバー
  { r: 0, g: 155, b: 191 },    // T 東西線スカイ
  { r: 0, g: 187, b: 133 },    // C 千代田線グリーン
  { r: 143, g: 118, b: 214 },  // Z 半蔵門線パープル（人口が多い）
];

/**
 * ストライプにする段の番号。青(T 東西線スカイ)。
 *
 * CUD 配色では青と緑(C 千代田線グリーン)が隣接しており、不透明度を
 * 0.35 まで下げた状態では色だけの判別が苦しい。白とのストライプにして
 * 色以外の手がかりを足す。
 *
 * 東京メトロが路線記号(G/M/H/T/C/Y/Z/N/F)を足したのと同じ考え方で、
 * 「色だけに情報を載せない」という CUD の基本に沿う。
 */
export const STRIPED_BAND_INDEX = 3;

/** スプライトに登録する画像IDの接頭辞。他の画像と衝突させない。 */
export const PATTERN_ID_PREFIX = 'mesh-pattern-';

/** タイルの一辺(px)。小さすぎるとぼやけ、大きすぎると縞が粗くなる。 */
const TILE_SIZE = 16;

/** 段ごとの画像IDを返す。 */
export function patternIds() {
  return DENSITY_COLORS_CUD.map((_, index) => `${PATTERN_ID_PREFIX}${index}`);
}

/**
 * 塗りタイルを SVG の data URI で返す。
 *
 * @param {{r:number,g:number,b:number}} color 段の色
 * @param {boolean} striped true なら白地に斜めの縞、false なら単色
 * @returns {string} `map.imageSprite.add()` に渡せる data URI
 */
export function patternTileDataUri(color, striped) {
  const c = color || { r: 0, g: 0, b: 0 };
  const fill = `rgb(${c.r},${c.g},${c.b})`;
  const n = TILE_SIZE;

  let body;
  if (striped) {
    // 「(x + y) を n で割った余りが n/2 未満」の領域を塗ると 45度の縞になる。
    // 条件がタイル境界をまたいで連続するため、繰り返しても継ぎ目が出ない。
    //
    // 線(stroke)で描く方式は、タイルの外へはみ出した分の折り返しが合わず
    // 縞が途切れて風車のような模様になった（実際にそうなった）。
    // 領域を多角形で塗る方式なら境界条件がそのまま成立する。
    const h = n / 2;
    body =
      `<rect width="${n}" height="${n}" fill="#ffffff"/>` +
      `<path d="M0 0 L${h} 0 L0 ${h} Z" fill="${fill}"/>` +
      `<path d="M${n} 0 L${n} ${h} L${h} ${n} L0 ${n} Z" fill="${fill}"/>`;
  } else {
    body = `<rect width="${n}" height="${n}" fill="${fill}"/>`;
  }

  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${n}" height="${n}" ` +
    `viewBox="0 0 ${n} ${n}">${body}</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/**
 * Azure Maps の PolygonLayer に渡す fillPattern 式を組み立てる。
 *
 * 画像は補間できないため interpolate ではなく step を使う。
 * このため色は段ごとに切り替わり、既定配色のような連続変化にはならない。
 *
 * @param {string} [resolution]
 * @returns {Array} ['step', ['get','populationDensity'], id0, break1, id1, ...]
 */
export function buildFillPatternExpression(resolution) {
  const breaks =
    DENSITY_BREAKS_BY_RESOLUTION[resolution] ||
    DENSITY_BREAKS_BY_RESOLUTION[DEFAULT_RESOLUTION];
  const ids = patternIds();

  const expression = ['step', ['get', 'populationDensity'], ids[0]];
  for (let i = 1; i < breaks.length; i += 1) {
    expression.push(breaks[i], ids[i]);
  }
  return expression;
}

/**
 * 配色名から色の配列を返す。未知の名前は既定配色に倒す。
 *
 * localStorage の保存値が壊れていても地図が描けなくならないようにするため、
 * 例外を投げずにフォールバックする。
 *
 * @param {string} [palette] PALETTE_NORMAL / PALETTE_CUD
 */
export function getDensityColors(palette) {
  return palette === PALETTE_CUD ? DENSITY_COLORS_CUD : DENSITY_COLORS;
}

/**
 * 解像度ごとの区切り値（セル内の人口, 人）。
 *
 * 実データ（横浜・川崎・相模原）の分布に合わせてあり、
 * 中央値が2段目に収まるよう調整している。
 *   1km  中央値 4,291 / 最大 30,691
 *   500m 中央値 1,333 / 最大 13,089
 *   250m 中央値   416 / 最大  6,923
 *   125m 中央値   124 / 最大  2,966
 */
export const DENSITY_BREAKS_BY_RESOLUTION = {
  '1km': [0, 2000, 5000, 10000, 20000, 30000],
  '500m': [0, 1000, 2500, 5000, 10000, 15000],
  // 250m 以下は実データの最大値が小さく（250m:6,923人 / 125m:2,966人）、
  // 単純に半減させた区切りだと大半のセルが最下段に集まって緑一色になる。
  // 各段におおむね均等に散るよう、分布（95パーセンタイル基準）に合わせてある。
  '250m': [0, 250, 500, 900, 1400, 2200],
  '125m': [0, 80, 160, 280, 450, 700],
};

/** 解像度が不明なときに使う既定。 */
export const DEFAULT_RESOLUTION = '1km';

export const MIN_OPACITY = 0.15;
export const MAX_OPACITY = 0.75;

/**
 * CUD 配色で使う固定の不透明度。
 *
 * 既定配色は人口に応じて 0.15〜0.75 と濃さを変える（地図が透けるように）。
 * だが CUD 配色で不透明度を変えると、低密度側が地図背景に溶けて
 * 隣接段が見分けられなくなる（実測 ΔE 4.0、判別限界は約10）。
 * そのため人口によらず固定する。
 *
 * 値は「地図が読める程度に薄く、かつ段が見分けられる」範囲で選ぶ。
 * 路線カラーは互いに大きく離れているため薄くしても余裕がある。
 * 地図背景と合成した実測 ΔE:
 *
 *   0.85  正常 38.9 / P型 27.8 / D型 37.7   濃すぎて地図が見えにくい
 *   0.55  正常 29.0 / P型 17.9 / D型 23.7
 *   0.35  正常 17.9 / P型 11.4 / D型 14.2   ← 採用（地図の見やすさを優先）
 *   0.25  正常 12.5 / P型  9.1 / D型  9.8   判別限界を割る
 *
 * 0.35 は判別限界(約10)に対する余裕が P型で 1.4 しかない。これ以上
 * 下げないこと。なお既定配色(緑→黄→赤)は P型 3.6 なので、薄くした
 * この状態でも既定より3倍以上見分けやすい。
 */
export const CUD_OPACITY = 0.35;

/**
 * 解像度に対応する停止点（区切り値 + 色）を返す。
 * 未知の解像度は既定にフォールバックする。
 */
export function getDensityStops(resolution, palette) {
  const breaks =
    DENSITY_BREAKS_BY_RESOLUTION[resolution] ||
    DENSITY_BREAKS_BY_RESOLUTION[DEFAULT_RESOLUTION];
  const colors = getDensityColors(palette);
  return breaks.map((density, index) => ({ density, rgb: colors[index] }));
}

/** 数値以外・範囲外を安全な値に丸める。 */
function normalizeDensity(density, stops) {
  const min = stops[0].density;
  const max = stops[stops.length - 1].density;
  const value = Number(density);
  if (!Number.isFinite(value)) return min;
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

/**
 * 人口に対応する RGB を返す。
 * @param {number} density セル内の人口
 * @param {string} [resolution] "1km" / "500m" / "250m" / "125m"
 * @param {string} [palette] PALETTE_NORMAL / PALETTE_CUD
 * @returns {{r: number, g: number, b: number}} 各成分 0-255 の整数
 */
export function densityToRgb(density, resolution, palette) {
  const stops = getDensityStops(resolution, palette);
  const value = normalizeDensity(density, stops);

  for (let i = 1; i < stops.length; i += 1) {
    const lower = stops[i - 1];
    const upper = stops[i];
    if (value <= upper.density) {
      const span = upper.density - lower.density;
      const t = span === 0 ? 0 : (value - lower.density) / span;
      return {
        r: Math.round(lerp(lower.rgb.r, upper.rgb.r, t)),
        g: Math.round(lerp(lower.rgb.g, upper.rgb.g, t)),
        b: Math.round(lerp(lower.rgb.b, upper.rgb.b, t)),
      };
    }
  }

  return { ...stops[stops.length - 1].rgb };
}

/** CSS の rgb() 文字列を返す。 */
export function densityToColor(density, resolution, palette) {
  const { r, g, b } = densityToRgb(density, resolution, palette);
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * 人口に対応する不透明度を返す。
 *
 * CUD 配色では濃淡が情報そのものなので、不透明度は変えず固定する。
 *
 * @param {number} density セル内の人口
 * @param {string} [resolution]
 * @param {string} [palette] PALETTE_NORMAL / PALETTE_CUD
 */
export function densityToOpacity(density, resolution, palette) {
  if (palette === PALETTE_CUD) return CUD_OPACITY;

  const stops = getDensityStops(resolution);
  const min = stops[0].density;
  const max = stops[stops.length - 1].density;
  const value = normalizeDensity(density, stops);
  const t = max === min ? 0 : (value - min) / (max - min);
  return MIN_OPACITY + (MAX_OPACITY - MIN_OPACITY) * t;
}

/**
 * Azure Maps の PolygonLayer に渡す fillColor 式を組み立てる。
 * @param {string} [resolution]
 * @param {string} [palette] PALETTE_NORMAL / PALETTE_CUD
 * @returns {Array} ['interpolate', ['linear'], ['get', 'populationDensity'], d0, c0, ...]
 */
export function buildFillColorExpression(resolution, palette) {
  const expression = ['interpolate', ['linear'], ['get', 'populationDensity']];
  for (const stop of getDensityStops(resolution, palette)) {
    const { r, g, b } = stop.rgb;
    expression.push(stop.density, `rgb(${r}, ${g}, ${b})`);
  }
  return expression;
}

/**
 * Azure Maps の PolygonLayer に渡す fillOpacity 式を組み立てる。
 *
 * @param {string} [resolution]
 * @param {string} [palette] PALETTE_NORMAL / PALETTE_CUD
 */
export function buildFillOpacityExpression(resolution, palette) {
  const expression = ['interpolate', ['linear'], ['get', 'populationDensity']];
  for (const stop of getDensityStops(resolution, palette)) {
    expression.push(stop.density, densityToOpacity(stop.density, resolution, palette));
  }
  return expression;
}
