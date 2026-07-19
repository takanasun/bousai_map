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
 * CUD 配色（東京メトロ路線カラー G/M/H/T/C/Z）は互いに最大限離した
 * カテゴリ配色のため、隣接段の見分けが良い。地図背景と合成した実測:
 *
 *   正常 38.9 / P型 27.8 / D型 37.7
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
  buildFillOpacityExpression,
  densityToOpacity,
  CUD_OPACITY,
  MIN_OPACITY,
  MAX_OPACITY,
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

  // 【設計判断の記録】
  // 以前の CUD 配色（cividis / 銀座線→半蔵門線の再構成）は「明度が単調に下がる」
  // ことを保証していた。色相を区別できなくても濃淡だけで順序を読めるからである。
  //
  // 現在は東京メトロの路線カラー6色をそのまま採っており、明度は単調ではない
  // （L* = 74, 57, 77, 64, 71, 62）。カテゴリ配色を順序尺度に使う以上、
  // 「どちらが混雑しているか」を色だけで判断することはできず、凡例との照合が要る。
  // その代わり隣接段の見分けは大きく改善している（D型 13.1 → 37.7）。
  //
  // したがってここで守るべき性質は「単調性」ではなく「互いに紛れないこと」。
  it('どの2色も互いに紛れない', () => {
    // 路線カラーは9路線を区別するための配色なので、どのペアも十分離れている
    for (let i = 0; i < DENSITY_COLORS_CUD.length; i += 1) {
      for (let j = i + 1; j < DENSITY_COLORS_CUD.length; j += 1) {
        const a = DENSITY_COLORS_CUD[i];
        const b = DENSITY_COLORS_CUD[j];
        const distance = Math.hypot(a.r - b.r, a.g - b.g, a.b - b.b);
        expect(distance).toBeGreaterThan(60);
      }
    }
  });

  it('既定配色より隣接段が見分けやすい', () => {
    // 「見分けづらい」という指摘を受けて入れ替えた経緯があるため、
    // 既定配色より悪い配色に戻ってしまわないよう下限を固定する。
    const minGap = (colors) =>
      Math.min(
        ...colors.slice(0, -1).map((c, i) => {
          const n = colors[i + 1];
          return Math.hypot(c.r - n.r, c.g - n.g, c.b - n.b);
        }),
      );
    expect(minGap(DENSITY_COLORS_CUD)).toBeGreaterThan(minGap(DENSITY_COLORS));
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

  it('区切りごとに異なる色を返す', () => {
    // 明度は単調でないため（上のコメント参照）、順序ではなく
    // 「段が変われば色が変わる」ことを担保する。
    const colors = [0, 2000, 5000, 10000, 20000, 30000].map((d) =>
      JSON.stringify(densityToRgb(d, '1km', PALETTE_CUD)),
    );
    expect(new Set(colors).size).toBe(colors.length);
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


/**
 * 不透明度の扱い。
 *
 * 既定配色は人口に応じて 0.15〜0.75 で濃くしている（地図が透けて見えるように）。
 * だが CUD 配色は「明度で人口を読ませる」設計のため、不透明度で明度を
 * 上書きすると原理的に成立しない。
 *
 * 地図背景と合成した実測 ΔE:
 *   可変不透明度  正常 4.0 / P型 4.3 / D型 3.9   ← 判別限界(約10)を大きく下回る
 *   固定 0.85     正常 12.9 / P型 12.8 / D型 13.1
 */
describe('CUD 配色の不透明度', () => {
  it('CUD では人口によらず一定', () => {
    const values = [0, 5000, 15000, 30000].map((d) =>
      densityToOpacity(d, '1km', PALETTE_CUD),
    );
    for (const v of values) {
      expect(v).toBe(CUD_OPACITY);
    }
  });

  it('既定配色では従来どおり人口に応じて変わる', () => {
    const low = densityToOpacity(0, '1km', PALETTE_NORMAL);
    const high = densityToOpacity(30000, '1km', PALETTE_NORMAL);
    expect(low).toBeCloseTo(MIN_OPACITY, 5);
    expect(high).toBeCloseTo(MAX_OPACITY, 5);
    expect(high).toBeGreaterThan(low);
  });

  it('薄すぎると色が判別できないため十分な濃さがある', () => {
    // 0.6 では ΔE が 8.4 まで落ちて判別限界を割る
    expect(CUD_OPACITY).toBeGreaterThanOrEqual(0.8);
    // 1.0 にすると地図が完全に隠れて位置が分からなくなる
    expect(CUD_OPACITY).toBeLessThan(1);
  });

  it('CUD の不透明度式は全区切りで同じ値を返す', () => {
    const expr = buildFillOpacityExpression('1km', PALETTE_CUD);
    // ['interpolate', ['linear'], ['get', ...], d0, o0, d1, o1, ...]
    const opacities = expr.slice(3).filter((_, i) => i % 2 === 1);
    expect(opacities.length).toBeGreaterThan(1);
    for (const o of opacities) {
      expect(o).toBe(CUD_OPACITY);
    }
  });

  it('既定配色の不透明度式は段ごとに増える', () => {
    const expr = buildFillOpacityExpression('1km', PALETTE_NORMAL);
    const opacities = expr.slice(3).filter((_, i) => i % 2 === 1);
    for (let i = 0; i < opacities.length - 1; i += 1) {
      expect(opacities[i + 1]).toBeGreaterThan(opacities[i]);
    }
  });
});
