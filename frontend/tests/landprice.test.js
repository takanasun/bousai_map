/**
 * 地価フィルタのロジックのテスト。
 *
 * スライダーで指定した上限以下のエリアだけを強調する。
 */

import { describe, it, expect } from 'vitest';
import {
  DEFAULT_PRICE_RANGE,
  formatPrice,
  resolvePriceRange,
  filterLandsByMaxPrice,
  filterAreasByMaxPrice,
  landsToFeatureCollection,
  buildLandPriceColorExpression,
} from '../src/landprice.js';

const LANDS = [
  { id: 'l1', town: '千鳥町', pricePerSqm: 92000, location: { lat: 35.5, lng: 139.75 }, uses: ['工場'] },
  { id: 'l2', town: '中瀬', pricePerSqm: 272000, location: { lat: 35.512, lng: 139.715 }, uses: ['住宅'] },
  { id: 'l3', town: '駅前本町', pricePerSqm: 4850000, location: { lat: 35.531, lng: 139.697 }, uses: ['店舗'] },
  { id: 'l4', town: '座標なし', pricePerSqm: 100000 },
];

const AREAS = [
  { town: '千鳥町', avgPricePerSqm: 92000, sheltersNearby: 0 },
  { town: '中瀬', avgPricePerSqm: 286000, sheltersNearby: 2 },
  { town: '駅前本町', avgPricePerSqm: 4850000, sheltersNearby: 3 },
];

describe('formatPrice', () => {
  it('万円単位で読みやすく整形する', () => {
    expect(formatPrice(150000)).toBe('15万円');
    expect(formatPrice(4850000)).toBe('485万円');
  });

  it('端数は丸める（スライダーの目盛り表示用）', () => {
    expect(formatPrice(92000)).toBe('9万円');
  });

  it('0 や不正値でも落ちない', () => {
    expect(formatPrice(0)).toBe('0万円');
    expect(formatPrice(null)).toBe('0万円');
  });
});

describe('resolvePriceRange', () => {
  it('サーバの実データ範囲を使う', () => {
    expect(resolvePriceRange({ min: 92000, max: 4850000 })).toEqual({
      min: 92000,
      max: 4850000,
    });
  });

  it('範囲が無い・不正なら既定値にフォールバックする', () => {
    expect(resolvePriceRange(null)).toEqual(DEFAULT_PRICE_RANGE);
    expect(resolvePriceRange({ min: 0, max: 0 })).toEqual(DEFAULT_PRICE_RANGE);
  });

  it('min > max の壊れた範囲も既定値に落とす', () => {
    expect(resolvePriceRange({ min: 900, max: 100 })).toEqual(DEFAULT_PRICE_RANGE);
  });
});

describe('filterLandsByMaxPrice', () => {
  it('上限以下の地点だけ返す', () => {
    expect(filterLandsByMaxPrice(LANDS, 300000).map((l) => l.id)).toEqual(['l1', 'l2', 'l4']);
  });

  it('上限なしなら全件', () => {
    expect(filterLandsByMaxPrice(LANDS, null)).toHaveLength(4);
  });

  it('元の配列を破壊しない', () => {
    const copy = JSON.parse(JSON.stringify(LANDS));
    filterLandsByMaxPrice(LANDS, 100000);
    expect(LANDS).toEqual(copy);
  });

  it('null / 空でも落ちない', () => {
    expect(filterLandsByMaxPrice(null, 100)).toEqual([]);
  });
});

describe('filterAreasByMaxPrice', () => {
  it('平均地価が上限以下のエリアだけ返す', () => {
    expect(filterAreasByMaxPrice(AREAS, 300000).map((a) => a.town)).toEqual(['千鳥町', '中瀬']);
  });

  it('上限なしなら全件', () => {
    expect(filterAreasByMaxPrice(AREAS, null)).toHaveLength(3);
  });
});

describe('landsToFeatureCollection', () => {
  it('Point Feature に変換し、座標は [lng, lat] 順', () => {
    const [f] = landsToFeatureCollection([LANDS[0]]).features;
    expect(f.geometry.coordinates).toEqual([139.75, 35.5]);
  });

  it('価格と町名を properties に持つ（色分けとポップアップで使う）', () => {
    const [f] = landsToFeatureCollection([LANDS[0]]).features;
    expect(f.properties.pricePerSqm).toBe(92000);
    expect(f.properties.town).toBe('千鳥町');
  });

  it('座標が無い地点は除外する', () => {
    expect(landsToFeatureCollection(LANDS).features).toHaveLength(3);
  });

  it('空配列・null でも落ちない', () => {
    expect(landsToFeatureCollection(null).features).toEqual([]);
  });
});

describe('buildLandPriceColorExpression', () => {
  it('pricePerSqm を参照する interpolate 式', () => {
    const expr = buildLandPriceColorExpression({ min: 92000, max: 4850000 });
    expect(expr[0]).toBe('interpolate');
    expect(expr[2]).toEqual(['get', 'pricePerSqm']);
  });

  it('停止点は価格の昇順で並ぶ', () => {
    const expr = buildLandPriceColorExpression({ min: 92000, max: 4850000 });
    const stops = [];
    for (let i = 3; i < expr.length; i += 2) stops.push(expr[i]);
    expect(stops).toEqual([...stops].sort((a, b) => a - b));
  });

  it('範囲が潰れていても停止点が重複しない（interpolate はエラーになる）', () => {
    const expr = buildLandPriceColorExpression({ min: 100, max: 100 });
    const stops = [];
    for (let i = 3; i < expr.length; i += 2) stops.push(expr[i]);
    expect(new Set(stops).size).toBe(stops.length);
  });
});

// ---------------------------------------------------------------------------
// 色分けの区切り
//
// 県全域の地価は 1,170〜15,600,000円（13,000倍）で、中央値は190,000円。
// 最小〜最大を線形に分けると 99.8% が最下段に潰れて全部同じ色になる。
// 実データのパーセンタイルに合わせた区切りを使う。
// ---------------------------------------------------------------------------

import { PRICE_BREAKS, PRICE_COLORS, buildPriceBreaks } from '../src/landprice.js';

describe('PRICE_BREAKS', () => {
  it('色の数と区切りの数が一致する', () => {
    expect(PRICE_BREAKS).toHaveLength(PRICE_COLORS.length);
  });

  it('昇順に並ぶ', () => {
    expect(PRICE_BREAKS).toEqual([...PRICE_BREAKS].sort((a, b) => a - b));
  });

  it('中央値(19万円)が中間の段に入る（全部同じ色にならない）', () => {
    const median = 190000;
    const index = PRICE_BREAKS.findIndex((b) => median <= b);
    expect(index).toBeGreaterThan(0);
    expect(index).toBeLessThan(PRICE_BREAKS.length - 1);
  });

  it('上限が最大値より十分低い（外れ値に引っ張られない）', () => {
    expect(PRICE_BREAKS[PRICE_BREAKS.length - 1]).toBeLessThan(2000000);
  });
});

describe('buildPriceBreaks', () => {
  it('実データの分布から区切りを作る', () => {
    const prices = Array.from({ length: 100 }, (_, i) => (i + 1) * 1000);
    const breaks = buildPriceBreaks(prices);
    expect(breaks).toHaveLength(PRICE_COLORS.length);
    expect(breaks).toEqual([...breaks].sort((a, b) => a - b));
  });

  it('外れ値があっても上位帯が潰れない', () => {
    const prices = [...Array.from({ length: 99 }, () => 100000), 99000000];
    const breaks = buildPriceBreaks(prices);
    // 外れ値に引っ張られて最大が1億近くにならないこと
    expect(breaks[breaks.length - 1]).toBeLessThan(1000000);
  });

  it('停止点が重複しない（interpolate はエラーになる）', () => {
    const breaks = buildPriceBreaks([100, 100, 100, 100]);
    expect(new Set(breaks).size).toBe(breaks.length);
  });

  it('空配列なら既定の区切りを返す', () => {
    expect(buildPriceBreaks([])).toEqual(PRICE_BREAKS);
    expect(buildPriceBreaks(null)).toEqual(PRICE_BREAKS);
  });
});

describe('buildLandPriceColorExpression（分布ベース）', () => {
  it('価格配列を渡すとその分布で色を分ける', () => {
    const prices = Array.from({ length: 100 }, (_, i) => (i + 1) * 1000);
    const expr = buildLandPriceColorExpression(prices);
    const stops = [];
    for (let i = 3; i < expr.length; i += 2) stops.push(expr[i]);
    expect(stops).toEqual([...stops].sort((a, b) => a - b));
    expect(new Set(stops).size).toBe(stops.length);
  });

  it('引数なしでも既定の区切りで動く', () => {
    const expr = buildLandPriceColorExpression();
    expect(expr[0]).toBe('interpolate');
    expect(expr[2]).toEqual(['get', 'pricePerSqm']);
  });
});

// ---------------------------------------------------------------------------
// 正方形アイコンでの表示
//
// 円(BubbleLayer)だとトイレの丸ピンと紛らわしいため四角にする。
// 色は価格帯ごとに用意した画像を切り替えて表現する。
// ---------------------------------------------------------------------------

import {
  PRICE_ICON_PREFIX,
  priceBandIndex,
  priceIconIds,
  buildLandPriceIconExpression,
} from '../src/landprice.js';

describe('priceBandIndex', () => {
  const breaks = [80000, 140000, 200000, 300000, 500000];

  it('区切りに応じた段のインデックスを返す', () => {
    // breaks[i] は段の「下限」。段i = [breaks[i], breaks[i+1]) の範囲。
    expect(priceBandIndex(50000, breaks)).toBe(0);   // 最下限未満も段0
    expect(priceBandIndex(100000, breaks)).toBe(0);  // 80,000〜140,000
    expect(priceBandIndex(150000, breaks)).toBe(1);  // 140,000〜200,000
    expect(priceBandIndex(1000000, breaks)).toBe(4); // 500,000〜
  });

  it('区切りちょうどの値はその段の下限として扱う', () => {
    expect(priceBandIndex(140000, breaks)).toBe(1);
    expect(priceBandIndex(500000, breaks)).toBe(4);
  });

  it('段数は色数を超えない', () => {
    for (const price of [0, 1, 99999999]) {
      const index = priceBandIndex(price, breaks);
      expect(index).toBeGreaterThanOrEqual(0);
      expect(index).toBeLessThan(PRICE_COLORS.length);
    }
  });

  it('不正な値は最下段にする（描画から消さない）', () => {
    expect(priceBandIndex(null, breaks)).toBe(0);
    expect(priceBandIndex(NaN, breaks)).toBe(0);
  });
});

describe('priceIconIds', () => {
  it('色数と同じだけのアイコンIDを返す', () => {
    expect(priceIconIds()).toHaveLength(PRICE_COLORS.length);
  });

  it('IDが重複しない', () => {
    const ids = priceIconIds();
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('共通の接頭辞を持つ（他レイヤーのアイコンと衝突しない）', () => {
    expect(priceIconIds().every((id) => id.startsWith(PRICE_ICON_PREFIX))).toBe(true);
  });
});

describe('buildLandPriceIconExpression', () => {
  it('priceBand プロパティでアイコンを出し分ける', () => {
    const expr = buildLandPriceIconExpression();
    expect(expr[0]).toBe('match');
    expect(expr[1]).toEqual(['get', 'priceBand']);
    expect(JSON.stringify(expr)).toContain(PRICE_ICON_PREFIX);
  });

  it('全ての段に対応するアイコンがある', () => {
    const expr = buildLandPriceIconExpression();
    for (const id of priceIconIds()) {
      expect(JSON.stringify(expr)).toContain(id);
    }
  });
});

describe('landsToFeatureCollection（価格帯の付与）', () => {
  it('priceBand を properties に付ける（レイヤーの出し分けに使う）', () => {
    const breaks = [80000, 140000, 200000, 300000, 500000];
    const fc = landsToFeatureCollection(LANDS, breaks);
    const byId = Object.fromEntries(fc.features.map((f) => [f.properties.id, f.properties]));
    expect(byId.l1.priceBand).toBe(0);       // 92,000円（80,000〜140,000）
    expect(byId.l3.priceBand).toBe(4);       // 4,850,000円
  });

  it('区切りを渡さなくても priceBand が付く', () => {
    const [f] = landsToFeatureCollection([LANDS[0]]).features;
    expect(typeof f.properties.priceBand).toBe('number');
  });
});

// ---------------------------------------------------------------------------
// 正方形アイコンの生成
//
// Azure Maps の createFromTemplate はテンプレート名の解決に依存し、
// 失敗すると初期化ごと落ちる。自前の SVG を data URI で登録すれば
// SDK のテンプレート事情に左右されない。
// ---------------------------------------------------------------------------

import { squareIconDataUri } from '../src/landprice.js';

describe('squareIconDataUri', () => {
  it('data URI 形式のSVGを返す', () => {
    const uri = squareIconDataUri({ r: 33, g: 102, b: 172 });
    expect(uri.startsWith('data:image/svg+xml')).toBe(true);
  });

  it('指定した色が含まれる', () => {
    const uri = squareIconDataUri({ r: 12, g: 34, b: 56 });
    expect(decodeURIComponent(uri)).toContain('rgb(12,34,56)');
  });

  it('正方形（rect）を描く', () => {
    const svg = decodeURIComponent(squareIconDataUri({ r: 1, g: 2, b: 3 }));
    expect(svg).toContain('<rect');
    expect(svg).toContain('<svg');
  });

  it('地図に埋もれないよう白い縁を持つ', () => {
    const svg = decodeURIComponent(squareIconDataUri({ r: 1, g: 2, b: 3 }));
    expect(svg).toContain('stroke="#ffffff"');
  });

  it('URIとして安全にエンコードされている（# や < が生で残らない）', () => {
    const uri = squareIconDataUri({ r: 1, g: 2, b: 3 });
    const payload = uri.slice(uri.indexOf(',') + 1);
    expect(payload).not.toContain('<');
    expect(payload).not.toContain('#');
  });

  it('色が欠けていても落ちない', () => {
    expect(() => squareIconDataUri(null)).not.toThrow();
    expect(squareIconDataUri(null).startsWith('data:image/svg+xml')).toBe(true);
  });
});
