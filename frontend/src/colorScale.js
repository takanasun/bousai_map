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
 * カラーユニバーサルデザイン（CUD）配色。cividis を反転した薄い黄→濃紺。
 *
 * 既定の緑→黄→赤は、色覚特性のある方には両端が判別しにくい。
 * 二色覚シミュレーション(Viénot-Brettel-Mollon)で測った色差 ΔE:
 *
 *   配色              隣接段の最小(D型)   両端(D型)
 *   緑→黄→赤                 13.7        25.7   ← 6段がほぼ1色に潰れる
 *   cividis反転               15.7       142.3
 *
 * cividis は色覚特性のために設計された配色で、**明度が単調に変化する**。
 * 色相を区別できなくても「濃いほど人口が多い」と濃淡だけで読め、
 * 印刷やモノクロ表示にも耐える。
 *
 * 他の候補を採らなかった理由:
 *   viridis  … 数値は最良(18.1)だが、濃い紫の端が地図上で唐突に見える
 *   YlOrRd   … ColorBrewer は色覚安全とするが、6段だとP型で 7.6 まで落ちる
 *   YlGnBu   … 穏やかな青系だがP型で 8.7 と既定配色より悪化する
 *   GnBu     … 同様に 5.7 で論外
 */
export const DENSITY_COLORS_CUD = [
  { r: 254, g: 232, b: 56 },   // 薄い黄（人口が少ない）
  { r: 211, g: 193, b: 100 },  // 砂
  { r: 145, g: 138, b: 95 },   // 黄土
  { r: 87, g: 93, b: 109 },    // 灰青
  { r: 38, g: 69, b: 110 },    // 青
  { r: 0, g: 34, b: 78 },      // 濃紺（人口が多い）
];

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

/** Azure Maps の PolygonLayer に渡す fillOpacity 式を組み立てる。 */
export function buildFillOpacityExpression(resolution) {
  const expression = ['interpolate', ['linear'], ['get', 'populationDensity']];
  for (const stop of getDensityStops(resolution)) {
    expression.push(stop.density, densityToOpacity(stop.density, resolution));
  }
  return expression;
}
