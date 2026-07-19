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
 * 解像度に対応する停止点（区切り値 + 色）を返す。
 * 未知の解像度は既定にフォールバックする。
 */
export function getDensityStops(resolution) {
  const breaks =
    DENSITY_BREAKS_BY_RESOLUTION[resolution] ||
    DENSITY_BREAKS_BY_RESOLUTION[DEFAULT_RESOLUTION];
  return breaks.map((density, index) => ({ density, rgb: DENSITY_COLORS[index] }));
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
 * @returns {{r: number, g: number, b: number}} 各成分 0-255 の整数
 */
export function densityToRgb(density, resolution) {
  const stops = getDensityStops(resolution);
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
export function densityToColor(density, resolution) {
  const { r, g, b } = densityToRgb(density, resolution);
  return `rgb(${r}, ${g}, ${b})`;
}

/** 人口に対応する不透明度（MIN_OPACITY〜MAX_OPACITY）を返す。 */
export function densityToOpacity(density, resolution) {
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
 * @returns {Array} ['interpolate', ['linear'], ['get', 'populationDensity'], d0, c0, ...]
 */
export function buildFillColorExpression(resolution) {
  const expression = ['interpolate', ['linear'], ['get', 'populationDensity']];
  for (const stop of getDensityStops(resolution)) {
    const { r, g, b } = stop.rgb;
    expression.push(stop.density, `rgb(${r}, ${g}, ${b})`);
  }
  return expression;
}

/** Azure Maps の PolygonLayer に渡す fillOpacity 式を組み立てる。 */
export function buildFillOpacityExpression(resolution) {
  const expression = ['interpolate', ['linear'], ['get', 'populationDensity']];
  for (const stop of getDensityStops(resolution)) {
    expression.push(stop.density, densityToOpacity(stop.density, resolution));
  }
  return expression;
}
