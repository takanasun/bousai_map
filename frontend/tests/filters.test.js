/**
 * 「多機能トイレのみ」フィルタのテスト。
 *
 * 多機能トイレの定義はバックエンド `infra.filter_multifunction_toilets` に統一し、
 * 車椅子対応(accessible) **または** オストメイト対応(ostomate) を多機能として扱う。
 */

import { describe, it, expect } from 'vitest';
import {
  isMultifunctionToilet,
  filterToilets,
  buildToiletFilterExpression,
} from '../src/filters.js';

const TOILETS = [
  { id: 'a', attributes: { accessible: true, ostomate: true, open24h: true } },
  { id: 'b', attributes: { accessible: false, ostomate: true, open24h: false } },
  { id: 'c', attributes: { accessible: true, ostomate: false, open24h: false } },
  { id: 'd', attributes: { accessible: false, ostomate: false, open24h: false } },
];

describe('isMultifunctionToilet', () => {
  it('accessible または ostomate が true なら多機能', () => {
    expect(isMultifunctionToilet(TOILETS[0])).toBe(true); // 両方
    expect(isMultifunctionToilet(TOILETS[1])).toBe(true); // ostomate のみ
    expect(isMultifunctionToilet(TOILETS[2])).toBe(true); // accessible のみ
  });

  it('どちらも false なら多機能ではない', () => {
    expect(isMultifunctionToilet(TOILETS[3])).toBe(false);
  });

  it('attributes 欠損・null は非対応として扱う', () => {
    expect(isMultifunctionToilet({ id: 'x' })).toBe(false);
    expect(isMultifunctionToilet(null)).toBe(false);
  });
});

describe('filterToilets', () => {
  it('OFF のときは全件を返す', () => {
    expect(filterToilets(TOILETS, false)).toHaveLength(4);
  });

  it('ON のときは多機能トイレのみを返す', () => {
    const out = filterToilets(TOILETS, true);
    expect(out.map((t) => t.id)).toEqual(['a', 'b', 'c']);
  });

  it('ostomate だけ true のものも残る（バックエンド定義との統一）', () => {
    const out = filterToilets(TOILETS, true);
    expect(out.find((t) => t.id === 'b')).toBeDefined();
  });

  it('どちらも false のものは除外される', () => {
    const out = filterToilets(TOILETS, true);
    expect(out.find((t) => t.id === 'd')).toBeUndefined();
  });

  it('元の配列を破壊しない', () => {
    const copy = JSON.parse(JSON.stringify(TOILETS));
    filterToilets(TOILETS, true);
    expect(TOILETS).toEqual(copy);
  });

  it('attributes 欠損は非対応として扱う', () => {
    expect(filterToilets([{ id: 'x' }], true)).toEqual([]);
  });

  it('null / 空配列でも落ちない', () => {
    expect(filterToilets(null, true)).toEqual([]);
    expect(filterToilets([], true)).toEqual([]);
  });
});

describe('buildToiletFilterExpression', () => {
  it('OFF のときは null（フィルタなし）', () => {
    expect(buildToiletFilterExpression(false)).toBeNull();
  });

  it('ON のときは accessible または ostomate を通す式', () => {
    expect(buildToiletFilterExpression(true)).toEqual([
      'any',
      ['==', ['get', 'accessible'], true],
      ['==', ['get', 'ostomate'], true],
    ]);
  });
});
