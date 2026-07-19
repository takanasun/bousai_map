/**
 * カラーユニバーサルデザイン（CUD）配色のテスト。
 *
 * 人口密度の既定配色は緑→黄→赤で、色覚特性のある方には両端が
 * 判別しにくい。実測（Viénot-Brettel-Mollon による二色覚シミュレーション）で
 * 隣接段の色差 ΔE が下記まで潰れることを確認している。
 *
 *   正常   隣接最小 17.6 / 両端 114.4
 *   P型    隣接最小 13.2 / 両端  32.5
 *   D型    隣接最小 13.7 / 両端  25.7   ← 6段がほぼ1色に見える
 *
 * CUD 配色（viridis 反転・薄い黄→濃い紫）は明度が単調に変化するため、
 * 色相を区別できなくても濃淡で読める。
 *
 *   D型    隣接最小 18.1 / 両端 142.4
 *
 * ここでは「配色が壊れていないこと」を色の性質から検証する。
 */

import { describe, it, expect } from 'vitest';
import {
  DENSITY_COLORS,
  DENSITY_COLORS_CUD,
  PALETTE_NORMAL,
  PALETTE_CUD,
  getDensityColors,
  densityToRgb,
  buildFillColorExpression,
} from '../src/colorScale.js';

/** sRGB を相対輝度に変換する（WCAG の定義）。明度の単調性を測るのに使う。 */
function luminance({ r, g, b }) {
  const f = (v) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

describe('DENSITY_COLORS_CUD', () => {
  it('既定配色と同じ段数を持つ', () => {
    // 段数が違うと区切り値との対応が崩れる
    expect(DENSITY_COLORS_CUD).toHaveLength(DENSITY_COLORS.length);
  });

  it('明度が単調に下がる（色相を区別できなくても濃淡で読める）', () => {
    // これが CUD 配色の肝。既定の緑→黄→赤は黄で明度が跳ね上がるため
    // 単調にならず、色相が頼りになってしまう。
    const lums = DENSITY_COLORS_CUD.map(luminance);
    for (let i = 0; i < lums.length - 1; i += 1) {
      expect(lums[i]).toBeGreaterThan(lums[i + 1]);
    }
  });

  it('両端の明度差が十分にある', () => {
    const first = luminance(DENSITY_COLORS_CUD[0]);
    const last = luminance(DENSITY_COLORS_CUD[DENSITY_COLORS_CUD.length - 1]);
    expect(first - last).toBeGreaterThan(0.5);
  });

  it('人口が多いほど濃い（直感と一致する向き）', () => {
    expect(luminance(DENSITY_COLORS_CUD[0])).toBeGreaterThan(
      luminance(DENSITY_COLORS_CUD[DENSITY_COLORS_CUD.length - 1]),
    );
  });

  it('各成分が 0〜255 に収まる', () => {
    for (const c of DENSITY_COLORS_CUD) {
      for (const v of [c.r, c.g, c.b]) {
        expect(v).toBeGreaterThanOrEqual(0);
        expect(v).toBeLessThanOrEqual(255);
      }
    }
  });
});

describe('getDensityColors', () => {
  it('既定はこれまでの配色', () => {
    expect(getDensityColors(PALETTE_NORMAL)).toEqual(DENSITY_COLORS);
    expect(getDensityColors()).toEqual(DENSITY_COLORS);
  });

  it('CUD を指定すると CUD 配色', () => {
    expect(getDensityColors(PALETTE_CUD)).toEqual(DENSITY_COLORS_CUD);
  });

  it('未知の名前は既定配色に倒す', () => {
    // 保存値が壊れていても地図が真っ白にならないようにする
    expect(getDensityColors('unknown')).toEqual(DENSITY_COLORS);
    expect(getDensityColors(null)).toEqual(DENSITY_COLORS);
  });
});

describe('densityToRgb（配色の切り替え）', () => {
  it('同じ密度でも配色によって色が変わる', () => {
    const normal = densityToRgb(20000, '1km', PALETTE_NORMAL);
    const cud = densityToRgb(20000, '1km', PALETTE_CUD);
    expect(cud).not.toEqual(normal);
  });

  it('配色を省略すると既定配色になる（既存の呼び出しを壊さない）', () => {
    expect(densityToRgb(5000, '1km')).toEqual(densityToRgb(5000, '1km', PALETTE_NORMAL));
  });

  it('CUD 配色でも人口が増えるほど暗くなる', () => {
    const low = luminance(densityToRgb(0, '1km', PALETTE_CUD));
    const mid = luminance(densityToRgb(8000, '1km', PALETTE_CUD));
    const high = luminance(densityToRgb(30000, '1km', PALETTE_CUD));
    expect(low).toBeGreaterThan(mid);
    expect(mid).toBeGreaterThan(high);
  });
});

describe('buildFillColorExpression（配色の切り替え）', () => {
  it('配色によって式の中身が変わる', () => {
    const normal = JSON.stringify(buildFillColorExpression('1km', PALETTE_NORMAL));
    const cud = JSON.stringify(buildFillColorExpression('1km', PALETTE_CUD));
    expect(cud).not.toBe(normal);
  });

  it('どちらの配色でも式の形は同じ（区切り数が変わらない）', () => {
    const normal = buildFillColorExpression('1km', PALETTE_NORMAL);
    const cud = buildFillColorExpression('1km', PALETTE_CUD);
    expect(cud).toHaveLength(normal.length);
    expect(cud[0]).toBe(normal[0]);
  });
});
