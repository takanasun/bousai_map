/**
 * メッシュの塗りパターンのテスト。
 *
 * CUD 配色では青(東西線スカイ)と緑(千代田線グリーン)が隣接しており、
 * 不透明度を下げた状態では色だけの判別が苦しい。そこで青の段だけ
 * 白とのストライプにして、色以外の手がかりを足す。
 *
 * これは東京メトロが路線記号(G/M/H/T/C/Y/Z/N/F)を足したのと同じ考え方で、
 * 「色だけに情報を載せない」という CUD の基本に沿う。
 */

import { describe, it, expect } from 'vitest';
import {
  DENSITY_COLORS_CUD,
  STRIPED_BAND_INDEX,
  PATTERN_ID_PREFIX,
  patternIds,
  patternTileDataUri,
  buildFillPatternExpression,
  DENSITY_BREAKS_BY_RESOLUTION,
} from '../src/colorScale.js';

/** data URI から SVG 本文を取り出す。 */
function decode(uri) {
  return decodeURIComponent(uri.replace(/^data:image\/svg\+xml;charset=utf-8,/, ''));
}

describe('STRIPED_BAND_INDEX', () => {
  it('ストライプにするのは青(東西線スカイ)の段', () => {
    const striped = DENSITY_COLORS_CUD[STRIPED_BAND_INDEX];
    expect(striped).toEqual({ r: 0, g: 155, b: 191 });
  });

  it('段の範囲内を指している', () => {
    expect(STRIPED_BAND_INDEX).toBeGreaterThanOrEqual(0);
    expect(STRIPED_BAND_INDEX).toBeLessThan(DENSITY_COLORS_CUD.length);
  });
});

describe('patternTileDataUri', () => {
  it('SVG の data URI を返す', () => {
    const uri = patternTileDataUri({ r: 0, g: 155, b: 191 }, true);
    expect(uri.startsWith('data:image/svg+xml')).toBe(true);
    expect(decode(uri)).toContain('<svg');
  });

  it('ストライプ指定なしなら単色で塗りつぶす', () => {
    const svg = decode(patternTileDataUri({ r: 255, g: 149, b: 0 }, false));
    expect(svg).toContain('rgb(255,149,0)');
    // 線を引かない＝ストライプがない
    expect(svg).not.toContain('<path');
  });

  it('ストライプ指定ありなら白地に色の縞を塗る', () => {
    const svg = decode(patternTileDataUri({ r: 0, g: 155, b: 191 }, true));
    expect(svg).toContain('#ffffff');        // 下地の白
    expect(svg).toContain('rgb(0,155,191)'); // 縞の色
    expect(svg).toContain('<path');
  });

  it('縞は線ではなく領域の塗りで描く（継ぎ目対策）', () => {
    // 線(stroke)方式は、タイル外へはみ出した分の折り返しが合わず
    // 縞が途切れて風車状の模様になった。同じ失敗に戻らないよう固定する。
    const svg = decode(patternTileDataUri({ r: 0, g: 155, b: 191 }, true));
    expect(svg).not.toContain('stroke');
  });

  it('縞は2つの領域で構成される（タイル境界で条件が連続するため）', () => {
    // 「(x+y) を n で割った余りが n/2 未満」の領域は、タイル内で
    // 左上の三角形と右下の四角形の2つに分かれる。
    const svg = decode(patternTileDataUri({ r: 0, g: 155, b: 191 }, true));
    expect(svg.match(/<path/g)).toHaveLength(2);
    expect(svg).toContain('Z');  // 閉じた領域
  });

  it('タイルは正方形（繰り返しても継ぎ目が出ないように）', () => {
    const svg = decode(patternTileDataUri({ r: 0, g: 0, b: 0 }, true));
    const width = svg.match(/width="(\d+)"/)[1];
    const height = svg.match(/height="(\d+)"/)[1];
    expect(width).toBe(height);
  });

  it('同じ入力なら同じ結果（毎回別画像を登録しない）', () => {
    const color = { r: 0, g: 155, b: 191 };
    expect(patternTileDataUri(color, true)).toBe(patternTileDataUri(color, true));
  });
});

describe('patternIds', () => {
  it('段の数だけ ID を返す', () => {
    expect(patternIds()).toHaveLength(DENSITY_COLORS_CUD.length);
  });

  it('ID が重複しない（重複すると別の段が同じ絵になる）', () => {
    expect(new Set(patternIds()).size).toBe(patternIds().length);
  });

  it('他のスプライト画像と衝突しない接頭辞を持つ', () => {
    for (const id of patternIds()) {
      expect(id.startsWith(PATTERN_ID_PREFIX)).toBe(true);
    }
  });
});

describe('buildFillPatternExpression', () => {
  const expr = buildFillPatternExpression('1km');

  it('step 式で人口から段を選ぶ', () => {
    // interpolate だと画像は補間できないため step を使う
    expect(expr[0]).toBe('step');
    expect(expr[1]).toEqual(['get', 'populationDensity']);
  });

  it('最初の段は区切り未満すべてに適用される', () => {
    expect(expr[2]).toBe(patternIds()[0]);
  });

  it('区切りは昇順（step 式の要件）', () => {
    const stops = expr.slice(3).filter((_, i) => i % 2 === 0);
    for (let i = 0; i < stops.length - 1; i += 1) {
      expect(stops[i + 1]).toBeGreaterThan(stops[i]);
    }
  });

  it('解像度ごとの区切りを使う', () => {
    const stops = buildFillPatternExpression('500m')
      .slice(3)
      .filter((_, i) => i % 2 === 0);
    // 先頭の 0 は step の既定値側に入るため、2番目以降と一致する
    expect(stops).toEqual(DENSITY_BREAKS_BY_RESOLUTION['500m'].slice(1));
  });

  it('未知の解像度は既定にフォールバックする', () => {
    expect(buildFillPatternExpression('unknown')).toEqual(buildFillPatternExpression('1km'));
  });
});
