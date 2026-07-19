/**
 * 医療機関の表示制御ロジックのテスト。
 *
 * トイレ・避難所と見分けが付くこと、診療科で絞り込めることを担保する。
 */

import { describe, it, expect } from 'vitest';
import {
  HOSPITAL_ICON,
  hospitalIconDataUri,
  collectSpecialities,
  formatSpecialityLines,
  hasSpeciality,
  filterHospitals,
  buildHospitalFilterExpression,
} from '../src/hospitals.js';

const HOSPITALS = [
  { id: 'h1', name: '川崎中央病院', capabilities: ['内科', '循環器内科', '精神科'] },
  { id: 'h2', name: '港こころのクリニック', capabilities: ['精神科'] },
  { id: 'h3', name: '北部整形外科', capabilities: ['整形外科', '内科'] },
  { id: 'h4', name: '科目不明医院', capabilities: [] },
];

describe('アイコン', () => {
  it('トイレ・避難所と異なるアイコンを使う', () => {
    // トイレは pin-round-blue、避難所は pin-red/pin-blue/pin-round-red
    expect(HOSPITAL_ICON).not.toBe('pin-round-blue');
    expect(HOSPITAL_ICON).not.toBe('pin-red');
    expect(HOSPITAL_ICON).not.toBe('pin-blue');
  });
});

describe('collectSpecialities', () => {
  it('診療科を出現回数の多い順に集計する', () => {
    // 内科(2件)・精神科(2件) が 循環器内科(1件)・整形外科(1件) より前に来ること。
    // 内科と精神科は同数なので順序は locale 依存 — そこは検証しない。
    const list = collectSpecialities(HOSPITALS);
    const rank = (name) => list.indexOf(name);
    expect(Math.max(rank('内科'), rank('精神科'))).toBeLessThan(
      Math.min(rank('循環器内科'), rank('整形外科')),
    );
  });

  it('重複を排除する', () => {
    const list = collectSpecialities(HOSPITALS);
    expect(new Set(list).size).toBe(list.length);
  });

  it('件数の上限を指定できる（UIの選択肢が長くなりすぎないように）', () => {
    expect(collectSpecialities(HOSPITALS, 2)).toHaveLength(2);
  });

  it('null / 空配列でも落ちない', () => {
    expect(collectSpecialities(null)).toEqual([]);
    expect(collectSpecialities([])).toEqual([]);
  });
});

describe('hasSpeciality', () => {
  it('診療科を持つ施設を判定する', () => {
    expect(hasSpeciality(HOSPITALS[0], '精神科')).toBe(true);
    expect(hasSpeciality(HOSPITALS[2], '精神科')).toBe(false);
  });

  it('capabilities 欠損は false', () => {
    expect(hasSpeciality({ id: 'x' }, '内科')).toBe(false);
    expect(hasSpeciality(null, '内科')).toBe(false);
  });
});

describe('filterHospitals', () => {
  it('診療科の指定がなければ全件', () => {
    expect(filterHospitals(HOSPITALS, '')).toHaveLength(4);
  });

  it('指定した診療科を持つ施設のみ返す', () => {
    const out = filterHospitals(HOSPITALS, '精神科');
    expect(out.map((h) => h.id)).toEqual(['h1', 'h2']);
  });

  it('診療科が無い施設は絞り込みで除外される', () => {
    const out = filterHospitals(HOSPITALS, '内科');
    expect(out.find((h) => h.id === 'h4')).toBeUndefined();
  });

  it('元の配列を破壊しない', () => {
    const copy = JSON.parse(JSON.stringify(HOSPITALS));
    filterHospitals(HOSPITALS, '内科');
    expect(HOSPITALS).toEqual(copy);
  });

  it('null / 空配列でも落ちない', () => {
    expect(filterHospitals(null, '内科')).toEqual([]);
    expect(filterHospitals([], '内科')).toEqual([]);
  });
});

describe('buildHospitalFilterExpression', () => {
  it('診療科の指定がなければ null（フィルタなし）', () => {
    expect(buildHospitalFilterExpression('')).toBeNull();
    expect(buildHospitalFilterExpression(undefined)).toBeNull();
  });

  it('指定時は capabilities 配列に含まれるかを判定する式', () => {
    const expr = buildHospitalFilterExpression('精神科');
    expect(expr).toEqual(['in', '精神科', ['get', 'capabilities']]);
  });
});

describe('formatSpecialityLines', () => {
  it('3件ごとに1行にまとめる', () => {
    const lines = formatSpecialityLines(['内科', '外科', '皮膚科', '眼科', '耳鼻科']);
    expect(lines).toEqual(['内科、外科、皮膚科', '眼科、耳鼻科']);
  });

  it('ちょうど3の倍数なら余りの行を作らない', () => {
    expect(formatSpecialityLines(['a', 'b', 'c'])).toEqual(['a、b、c']);
    expect(formatSpecialityLines(['a', 'b', 'c', 'd', 'e', 'f'])).toEqual(['a、b、c', 'd、e、f']);
  });

  it('1件でも配列を返す', () => {
    expect(formatSpecialityLines(['内科'])).toEqual(['内科']);
  });

  it('1行あたりの件数を変えられる', () => {
    expect(formatSpecialityLines(['a', 'b', 'c', 'd'], 2)).toEqual(['a、b', 'c、d']);
  });

  it('空・null は空配列', () => {
    expect(formatSpecialityLines([])).toEqual([]);
    expect(formatSpecialityLines(null)).toEqual([]);
  });

  it('空文字の診療科は除外する', () => {
    expect(formatSpecialityLines(['内科', '', '外科'])).toEqual(['内科、外科']);
  });

  it('perLine が 0 以下でも無限ループにならない', () => {
    expect(formatSpecialityLines(['a', 'b'], 0)).toEqual(['a、b']);
  });
});


describe('hospitalIconDataUri', () => {
  const uri = hospitalIconDataUri();
  // data URI はエンコードされているので、中身は復号してから調べる
  const svg = decodeURIComponent(uri.replace(/^data:image\/svg\+xml;charset=utf-8,/, ''));

  it('SVG の data URI を返す', () => {
    expect(uri.startsWith('data:image/svg+xml')).toBe(true);
    expect(svg).toContain('<svg');
    expect(svg).toContain('xmlns="http://www.w3.org/2000/svg"');
  });

  it('濃紺の円を描く（凡例チップと同じ色）', () => {
    expect(svg).toContain('<circle');
    expect(svg).toContain('#1a4d8f');
  });

  it('白い十字を描く', () => {
    // 縦棒と横棒で2本
    expect(svg.match(/fill="#ffffff"/g) || []).toHaveLength(2);
  });

  it('十字が円の中心に来る', () => {
    // 円の中心と、十字を構成する各矩形の中心が一致すること。
    // 組み込みピン + 文字重ねでは中心がずれたため、ここを固定する。
    const cx = Number(svg.match(/cx="([\d.]+)"/)[1]);
    const cy = Number(svg.match(/cy="([\d.]+)"/)[1]);
    const rects = [...svg.matchAll(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/g)];
    expect(rects).toHaveLength(2);
    for (const [, x, y, w, h] of rects) {
      expect(Number(x) + Number(w) / 2).toBeCloseTo(cx, 5);
      expect(Number(y) + Number(h) / 2).toBeCloseTo(cy, 5);
    }
  });

  it('十字が円からはみ出さない', () => {
    const cx = Number(svg.match(/cx="([\d.]+)"/)[1]);
    const r = Number(svg.match(/r="([\d.]+)"/)[1]);
    const rects = [...svg.matchAll(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/g)];
    for (const [, x, , w] of rects) {
      expect(Number(x)).toBeGreaterThanOrEqual(cx - r);
      expect(Number(x) + Number(w)).toBeLessThanOrEqual(cx + r);
    }
  });

  it('呼ぶたびに同じ内容を返す（毎回別画像を登録しない）', () => {
    expect(hospitalIconDataUri()).toBe(uri);
  });
});

describe('HOSPITAL_ICON', () => {
  it('組み込みピン名ではなく、自前で登録する画像IDを指す', () => {
    // 'pin-round-darkblue' は中央が白丸の組み込みピンで、
    // 上に十字を重ねると白丸に乗らずずれる。自前アイコンに置き換えた。
    expect(HOSPITAL_ICON).not.toContain('pin-round');
  });
});
