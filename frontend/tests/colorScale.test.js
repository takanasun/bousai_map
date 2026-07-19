/**
 * 人口密度 → 色/不透明度 の変換ロジックのテスト。
 *
 * 仕様 5.2:「人口が多いマス＝赤（不透明度高）〜 人口が少ないマス＝緑（不透明度低）」
 *
 * 値はセル内の人口（人）であり、セルが細かいほど値域が下がる。
 * そのため色の区切りは解像度ごとに切り替える。
 */

import { describe, it, expect } from 'vitest';
import {
  DENSITY_BREAKS_BY_RESOLUTION,
  DENSITY_COLORS,
  DEFAULT_RESOLUTION,
  MIN_OPACITY,
  MAX_OPACITY,
  getDensityStops,
  densityToRgb,
  densityToColor,
  densityToOpacity,
  buildFillColorExpression,
  buildFillOpacityExpression,
} from '../src/colorScale.js';

describe('解像度ごとの区切り値', () => {
  it('4段階すべてに区切りが定義されている', () => {
    expect(Object.keys(DENSITY_BREAKS_BY_RESOLUTION).sort()).toEqual(
      ['125m', '1km', '250m', '500m'].sort(),
    );
  });

  it('色の数と区切りの数が一致する', () => {
    for (const breaks of Object.values(DENSITY_BREAKS_BY_RESOLUTION)) {
      expect(breaks).toHaveLength(DENSITY_COLORS.length);
    }
  });

  it('区切りは昇順で先頭が0', () => {
    for (const breaks of Object.values(DENSITY_BREAKS_BY_RESOLUTION)) {
      expect(breaks[0]).toBe(0);
      expect(breaks).toEqual([...breaks].sort((a, b) => a - b));
    }
  });

  it('細かい解像度ほど区切りが小さい（セル面積が小さく人口も少ないため）', () => {
    const order = ['1km', '500m', '250m', '125m'];
    for (let i = 1; i < order.length; i += 1) {
      const coarser = DENSITY_BREAKS_BY_RESOLUTION[order[i - 1]];
      const finer = DENSITY_BREAKS_BY_RESOLUTION[order[i]];
      // 最大の区切りが必ず小さくなる
      expect(finer[finer.length - 1]).toBeLessThan(coarser[coarser.length - 1]);
    }
  });

  it('指定された区切り値のとおりであること', () => {
    expect(DENSITY_BREAKS_BY_RESOLUTION['500m']).toEqual([0, 1000, 2500, 5000, 10000, 15000]);
    // 250m 以下は実データの分布に合わせた値（緑一色になるのを防ぐため）
    expect(DENSITY_BREAKS_BY_RESOLUTION['250m']).toEqual([0, 250, 500, 900, 1400, 2200]);
    expect(DENSITY_BREAKS_BY_RESOLUTION['125m']).toEqual([0, 80, 160, 280, 450, 700]);
  });

  it('区切りの上限が実データの最大値から懸け離れていない', () => {
    // 上限が高すぎると濃い色が一度も使われず、濃淡が読めなくなる。
    // 実データの最大値: 1km 30,691 / 500m 13,089 / 250m 6,923 / 125m 2,966
    // 500m の上限 15,000 は最大値をやや上回るが、これは利用者の指定値。
    // 「桁が違うほど外れていない」ことを担保するため 1.2倍を上限とする。
    const observedMax = { '1km': 30691, '500m': 13089, '250m': 6923, '125m': 2966 };
    for (const [resolution, max] of Object.entries(observedMax)) {
      const breaks = DENSITY_BREAKS_BY_RESOLUTION[resolution];
      expect(breaks[breaks.length - 1]).toBeLessThanOrEqual(max * 1.2);
    }
  });
});

describe('getDensityStops', () => {
  it('解像度に応じた停止点を返す', () => {
    const stops = getDensityStops('500m');
    expect(stops.map((s) => s.density)).toEqual([0, 1000, 2500, 5000, 10000, 15000]);
    expect(stops[0].rgb).toEqual(DENSITY_COLORS[0]);
  });

  it('未知の解像度は既定にフォールバックする', () => {
    expect(getDensityStops('999m')).toEqual(getDensityStops(DEFAULT_RESOLUTION));
    expect(getDensityStops(undefined)).toEqual(getDensityStops(DEFAULT_RESOLUTION));
  });
});

describe('densityToRgb', () => {
  it('人口density=0 は緑（緑成分が赤成分より強い）', () => {
    const { r, g } = densityToRgb(0);
    expect(g).toBeGreaterThan(r);
  });

  it('最大densityは赤（赤成分が緑成分より強い）', () => {
    const breaks = DENSITY_BREAKS_BY_RESOLUTION[DEFAULT_RESOLUTION];
    const { r, g } = densityToRgb(breaks[breaks.length - 1]);
    expect(r).toBeGreaterThan(g);
  });

  it('densityが上がるほど「赤みの強さ」が単調に増す', () => {
    // 緑→黄→赤 のグラデーションでは中間の黄が緑成分のピークになるため、
    // 「緑成分が単調減少」は成立しない。緑←→赤 の度合いを表す r - g で評価する。
    const samples = [0, 1000, 2000, 5000, 10000, 20000, 30000];
    const redness = samples.map((d) => {
      const { r, g } = densityToRgb(d);
      return r - g;
    });
    for (let i = 1; i < redness.length; i += 1) {
      expect(redness[i]).toBeGreaterThan(redness[i - 1]);
    }
  });

  it('解像度を変えると同じ値でも色が変わる', () => {
    // 2500人は 1km では低密度（緑寄り）、250m では中位（黄〜橙寄り）
    const coarse = densityToRgb(2500, '1km');
    const fine = densityToRgb(2500, '250m');
    expect(fine.r - fine.g).toBeGreaterThan(coarse.r - coarse.g);
  });

  it('各成分は 0-255 の整数に収まる', () => {
    for (const d of [-100, 0, 3333, 999999]) {
      const { r, g, b } = densityToRgb(d);
      for (const v of [r, g, b]) {
        expect(Number.isInteger(v)).toBe(true);
        expect(v).toBeGreaterThanOrEqual(0);
        expect(v).toBeLessThanOrEqual(255);
      }
    }
  });

  it('範囲外の値はクランプされる', () => {
    const breaks = DENSITY_BREAKS_BY_RESOLUTION[DEFAULT_RESOLUTION];
    const max = breaks[breaks.length - 1];
    expect(densityToRgb(-500)).toEqual(densityToRgb(0));
    expect(densityToRgb(max + 100000)).toEqual(densityToRgb(max));
  });

  it('数値でない入力は最小density扱いにする（データ欠損で落ちない）', () => {
    const min = densityToRgb(0);
    expect(densityToRgb(null)).toEqual(min);
    expect(densityToRgb(undefined)).toEqual(min);
    expect(densityToRgb(NaN)).toEqual(min);
  });
});

describe('densityToColor', () => {
  it('CSSで使える rgb() 文字列を返す', () => {
    expect(densityToColor(10000)).toMatch(/^rgb\(\d{1,3}, \d{1,3}, \d{1,3}\)$/);
  });
});

describe('densityToOpacity', () => {
  it('人口が少ないほど不透明度は低く、多いほど高い', () => {
    expect(densityToOpacity(0)).toBeLessThan(densityToOpacity(30000));
  });

  it('MIN_OPACITY〜MAX_OPACITY の範囲に収まる', () => {
    for (const d of [-1, 0, 5000, 30000, 1e9]) {
      const o = densityToOpacity(d);
      expect(o).toBeGreaterThanOrEqual(MIN_OPACITY);
      expect(o).toBeLessThanOrEqual(MAX_OPACITY);
    }
  });

  it('解像度に応じて基準が変わる', () => {
    // 5000人は 250m では最大側、1km では中位
    expect(densityToOpacity(5000, '250m')).toBeGreaterThan(densityToOpacity(5000, '1km'));
  });
});

describe('Azure Maps 用の式', () => {
  it('fill色は populationDensity を参照する interpolate 式', () => {
    const expr = buildFillColorExpression();
    expect(expr[0]).toBe('interpolate');
    expect(expr[1]).toEqual(['linear']);
    expect(expr[2]).toEqual(['get', 'populationDensity']);
    expect(expr.length).toBe(3 + DENSITY_COLORS.length * 2);
  });

  it('解像度を渡すとその区切りで式を組む', () => {
    const expr = buildFillColorExpression('250m');
    const densities = [];
    for (let i = 3; i < expr.length; i += 2) densities.push(expr[i]);
    expect(densities).toEqual([0, 250, 500, 900, 1400, 2200]);
  });

  it('fill不透明度も populationDensity を参照する interpolate 式', () => {
    const expr = buildFillOpacityExpression();
    expect(expr[0]).toBe('interpolate');
    expect(expr[2]).toEqual(['get', 'populationDensity']);
  });

  it('色の停止点は density 昇順で並ぶ', () => {
    for (const resolution of Object.keys(DENSITY_BREAKS_BY_RESOLUTION)) {
      const expr = buildFillColorExpression(resolution);
      const densities = [];
      for (let i = 3; i < expr.length; i += 2) densities.push(expr[i]);
      expect(densities).toEqual([...densities].sort((a, b) => a - b));
    }
  });
});
