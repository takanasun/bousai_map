/**
 * 避難所の表示制御ロジックのテスト。
 *
 * 避難所は性質が異なる2種類があり、災害時の意味がまったく違う:
 *   指定緊急避難場所 … 切迫した危険から「命を守る」場所。災害種別ごとに指定。
 *   指定避難所       … 自宅に戻れない人が「生活する」場所。災害種別の指定なし。
 * 同一施設が兼ねる場合があるため、2つの真偽値で役割を表現する。
 */

import { describe, it, expect } from 'vitest';
import {
  DISASTER_LABELS,
  DISASTER_KEYS,
  shelterCategory,
  buildShelterIconExpression,
  buildShelterFilterExpression,
  filterShelters,
} from '../src/shelters.js';

const BOTH = {
  id: 'evac_001',
  name: '中央小学校体育館',
  isEmergencySite: true,
  isEvacuationCenter: true,
  isWelfareShelter: false,
  disasterTypes: ['flood', 'earthquake', 'fire'],
};
const CENTER_ONLY = {
  id: 'evac_002',
  name: '港福祉避難所',
  isEmergencySite: false,
  isEvacuationCenter: true,
  isWelfareShelter: true,
  disasterTypes: [],
};
const EMERGENCY_ONLY = {
  id: 'evac_003',
  name: '北部中学校グラウンド',
  isEmergencySite: true,
  isEvacuationCenter: false,
  isWelfareShelter: false,
  disasterTypes: ['earthquake', 'tsunami'],
};

const SHELTERS = [BOTH, CENTER_ONLY, EMERGENCY_ONLY];

describe('災害種別の定義', () => {
  it('災害対策基本法の8種別すべてにラベルがある', () => {
    expect(DISASTER_KEYS).toHaveLength(8);
    expect(Object.keys(DISASTER_LABELS).sort()).toEqual([...DISASTER_KEYS].sort());
  });

  it('バックエンド（clean_shelters.py）と同じキーを使う', () => {
    expect([...DISASTER_KEYS].sort()).toEqual(
      [
        'earthquake', 'fire', 'flood', 'inlandFlood',
        'landslide', 'stormSurge', 'tsunami', 'volcano',
      ].sort(),
    );
  });

  it('ラベルは日本語', () => {
    expect(DISASTER_LABELS.earthquake).toBe('地震');
    expect(DISASTER_LABELS.tsunami).toBe('津波');
    expect(DISASTER_LABELS.flood).toBe('洪水');
  });
});

describe('shelterCategory', () => {
  it('兼用・緊急のみ・避難所のみを区別する', () => {
    expect(shelterCategory(BOTH)).toBe('both');
    expect(shelterCategory(EMERGENCY_ONLY)).toBe('emergency');
    expect(shelterCategory(CENTER_ONLY)).toBe('center');
  });

  it('どちらでもない場合は unknown', () => {
    expect(shelterCategory({ id: 'x' })).toBe('unknown');
  });
});

describe('buildShelterIconExpression', () => {
  it('category プロパティで見た目を出し分ける case 式', () => {
    const expr = buildShelterIconExpression();
    expect(expr[0]).toBe('case');
    // 式の中で category を参照している
    expect(JSON.stringify(expr)).toContain('category');
  });
});

describe('filterShelters', () => {
  it('既定（両方表示・災害種別なし）では全件', () => {
    const out = filterShelters(SHELTERS, {
      showEmergency: true,
      showCenter: true,
      disasterType: '',
    });
    expect(out).toHaveLength(3);
  });

  it('緊急避難場所のみ表示', () => {
    const out = filterShelters(SHELTERS, {
      showEmergency: true,
      showCenter: false,
      disasterType: '',
    });
    expect(out.map((s) => s.id)).toEqual(['evac_001', 'evac_003']);
  });

  it('指定避難所のみ表示', () => {
    const out = filterShelters(SHELTERS, {
      showEmergency: false,
      showCenter: true,
      disasterType: '',
    });
    expect(out.map((s) => s.id)).toEqual(['evac_001', 'evac_002']);
  });

  it('どちらもOFFなら0件', () => {
    const out = filterShelters(SHELTERS, {
      showEmergency: false,
      showCenter: false,
      disasterType: '',
    });
    expect(out).toEqual([]);
  });

  it('災害種別で絞る（津波に対応する場所のみ）', () => {
    const out = filterShelters(SHELTERS, {
      showEmergency: true,
      showCenter: true,
      disasterType: 'tsunami',
    });
    expect(out.map((s) => s.id)).toEqual(['evac_003']);
  });

  it('災害種別の絞り込みは「生活する場所」を除外する', () => {
    // 指定避難所には災害種別の指定が無いため、種別で絞ると残らない
    const out = filterShelters([CENTER_ONLY], {
      showEmergency: true,
      showCenter: true,
      disasterType: 'earthquake',
    });
    expect(out).toEqual([]);
  });

  it('null / 空配列でも落ちない', () => {
    expect(filterShelters(null, { showEmergency: true, showCenter: true })).toEqual([]);
    expect(filterShelters([], { showEmergency: true, showCenter: true })).toEqual([]);
  });
});

describe('buildShelterFilterExpression', () => {
  it('両方表示・種別なしなら null（フィルタなし）', () => {
    expect(
      buildShelterFilterExpression({
        showEmergency: true,
        showCenter: true,
        disasterType: '',
      }),
    ).toBeNull();
  });

  it('どちらもOFFなら常に false になる式', () => {
    const expr = buildShelterFilterExpression({
      showEmergency: false,
      showCenter: false,
      disasterType: '',
    });
    expect(expr).toEqual(['==', ['literal', true], ['literal', false]]);
  });

  it('緊急避難場所のみなら isEmergencySite を見る式', () => {
    const expr = buildShelterFilterExpression({
      showEmergency: true,
      showCenter: false,
      disasterType: '',
    });
    expect(JSON.stringify(expr)).toContain('isEmergencySite');
    expect(JSON.stringify(expr)).not.toContain('isEvacuationCenter');
  });

  it('災害種別は平坦化したプロパティ（disaster_*）で判定する', () => {
    // 配列プロパティへの 'in' 演算子は SDK バージョン差が出るため、
    // GeoJSON 生成時に真偽値へ展開したものを参照する
    const expr = buildShelterFilterExpression({
      showEmergency: true,
      showCenter: true,
      disasterType: 'earthquake',
    });
    expect(JSON.stringify(expr)).toContain('disaster_earthquake');
  });
});
