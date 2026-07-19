/**
 * AIの回答に登場した施設を地図上で強調するための照合ロジックのテスト。
 *
 * AIにID列挙させるとハルシネーションの恐れがあるため、サーバが返した
 * 候補（AIに実際に渡した施設）の名前が回答文に現れたかで判定する。
 */

import { describe, it, expect } from 'vitest';
import {
  HIGHLIGHT_ICONS,
  matchMentionedFacilities,
  highlightsToFeatureCollection,
} from '../src/highlight.js';

const CANDIDATES = [
  { id: 's1', name: '東大島小学校', kind: 'shelter', location: { lat: 35.52, lng: 139.72 }, distanceKm: 0.18 },
  { id: 's2', name: 'さくら小学校', kind: 'shelter', location: { lat: 35.53, lng: 139.73 }, distanceKm: 0.5 },
  { id: 'h1', name: '川崎協同病院', kind: 'hospital', location: { lat: 35.524, lng: 139.721 }, distanceKm: 0.12 },
  { id: 't1', name: '中央公園トイレ', kind: 'toilet', location: { lat: 35.521, lng: 139.72 }, distanceKm: 0.3 },
  { id: 'x1', name: '', kind: 'shelter', location: { lat: 35.5, lng: 139.7 } },
];

describe('matchMentionedFacilities', () => {
  it('回答文に名前が現れた施設だけを返す', () => {
    const answer = '一番近い避難所は東大島小学校です。距離は0.181kmです。';
    expect(matchMentionedFacilities(answer, CANDIDATES).map((c) => c.id)).toEqual(['s1']);
  });

  it('複数該当すれば全て返す', () => {
    const answer = '東大島小学校と川崎協同病院が近いです。';
    const ids = matchMentionedFacilities(answer, CANDIDATES).map((c) => c.id);
    expect(ids).toEqual(['s1', 'h1']);
  });

  it('言及がなければ空配列', () => {
    expect(matchMentionedFacilities('該当する施設はありません。', CANDIDATES)).toEqual([]);
  });

  it('名前が空の候補は誤爆しない（空文字はどの文にも含まれるため）', () => {
    const hits = matchMentionedFacilities('なんらかの文章', CANDIDATES);
    expect(hits.find((c) => c.id === 'x1')).toBeUndefined();
  });

  it('座標が無い候補は除外する（地図に置けないため）', () => {
    const broken = [{ id: 'n1', name: 'テスト施設', kind: 'shelter' }];
    expect(matchMentionedFacilities('テスト施設です', broken)).toEqual([]);
  });

  it('同じ施設が何度出てきても重複しない', () => {
    const answer = '東大島小学校です。東大島小学校まで0.18km。東大島小学校を目指してください。';
    expect(matchMentionedFacilities(answer, CANDIDATES)).toHaveLength(1);
  });

  it('null / 空入力でも落ちない', () => {
    expect(matchMentionedFacilities('', CANDIDATES)).toEqual([]);
    expect(matchMentionedFacilities('文章', null)).toEqual([]);
    expect(matchMentionedFacilities(null, CANDIDATES)).toEqual([]);
  });
});

describe('HIGHLIGHT_ICONS', () => {
  it('種別ごとに旗の色を分ける', () => {
    expect(HIGHLIGHT_ICONS.shelter).toBeTruthy();
    expect(HIGHLIGHT_ICONS.hospital).toBeTruthy();
    expect(HIGHLIGHT_ICONS.toilet).toBeTruthy();
  });
});

describe('highlightsToFeatureCollection', () => {
  it('Point Feature に変換し、座標は [lng, lat] 順', () => {
    const fc = highlightsToFeatureCollection([CANDIDATES[0]]);
    expect(fc.features[0].geometry.coordinates).toEqual([139.72, 35.52]);
  });

  it('種別と名前を properties に持つ', () => {
    const [f] = highlightsToFeatureCollection([CANDIDATES[0]]).features;
    expect(f.properties.kind).toBe('shelter');
    expect(f.properties.name).toBe('東大島小学校');
  });

  it('空配列・null でも落ちない', () => {
    expect(highlightsToFeatureCollection([]).features).toEqual([]);
    expect(highlightsToFeatureCollection(null).features).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 距離での照合
//
// トイレは96%が既定名「公衆トイレ」で区別できず、AIは名前を書かずに
// 「距離: 0.117km」とだけ答えることがある。名前照合だけでは検出できない。
// ---------------------------------------------------------------------------

const TOILET_CANDIDATES = [
  { id: 't1', name: '公衆トイレ', kind: 'toilet', location: { lat: 35.52, lng: 139.72 }, distanceKm: 0.117 },
  { id: 't2', name: '公衆トイレ', kind: 'toilet', location: { lat: 35.53, lng: 139.73 }, distanceKm: 0.42 },
  { id: 's9', name: '中央小学校', kind: 'shelter', location: { lat: 35.51, lng: 139.71 }, distanceKm: 0.9 },
];

describe('距離による照合', () => {
  it('名前が無くても距離が一致すれば強調する', () => {
    const answer = '一番近いトイレは以下です。\n- 距離: 0.117km\n- 設備情報: なし';
    expect(matchMentionedFacilities(answer, TOILET_CANDIDATES).map((c) => c.id)).toEqual(['t1']);
  });

  it('末尾の0が省略された距離にも一致する（0.420km → 0.42km）', () => {
    const answer = '距離: 0.42km です。';
    expect(matchMentionedFacilities(answer, TOILET_CANDIDATES).map((c) => c.id)).toEqual(['t2']);
  });

  it('無関係な数字を距離と誤認しない', () => {
    const answer = '半径2kmの範囲に15件あります。';
    expect(matchMentionedFacilities(answer, TOILET_CANDIDATES)).toEqual([]);
  });

  it('名前一致と距離一致の両方を拾い、重複しない', () => {
    const answer = '中央小学校（距離: 0.9km）が最寄りです。';
    const hits = matchMentionedFacilities(answer, TOILET_CANDIDATES);
    expect(hits.map((c) => c.id)).toEqual(['s9']);
  });

  it('距離が無い候補は距離照合の対象外', () => {
    const noDistance = [{ id: 'x', name: '公衆トイレ', kind: 'toilet', location: { lat: 35.5, lng: 139.7 } }];
    expect(matchMentionedFacilities('距離: 0.117km', noDistance)).toEqual([]);
  });

  it('既定名だけの候補が名前照合で全件ヒットしない', () => {
    // 「公衆トイレ」という語が答えにあっても、1875件すべてを強調しては困る
    const answer = '公衆トイレをお探しですね。';
    const hits = matchMentionedFacilities(answer, TOILET_CANDIDATES);
    expect(hits.length).toBeLessThan(TOILET_CANDIDATES.length);
  });
});
