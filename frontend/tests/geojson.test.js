/**
 * API レスポンス → GeoJSON 変換のテスト。
 *
 * バックエンドの形式（docs/spec.md 4.1 / 4.4）を Azure Maps の DataSource が
 * 受け取れる GeoJSON FeatureCollection に変換する。
 */

import { describe, it, expect } from 'vitest';
import {
  meshToFeatureCollection,
  toiletsToFeatureCollection,
} from '../src/geojson.js';

// scripts/clean_mesh.py の出力と同じ形（閉じたリング5点, [lng, lat]）
const MESH_RECORD = {
  meshId: '53392537',
  coordinates: [
    [139.7125, 35.5208333],
    [139.725, 35.5208333],
    [139.725, 35.5291666],
    [139.7125, 35.5291666],
    [139.7125, 35.5208333],
  ],
  populationDensity: 20200,
};

// scripts/fetch_toilets.py の出力と同じ形
const TOILET_RECORD = {
  id: 'toilet_n10006253728',
  name: '公衆トイレ',
  location: { lat: 35.5216013, lng: 139.377736 },
  attributes: { accessible: false, ostomate: false, open24h: false },
};

describe('meshToFeatureCollection', () => {
  it('FeatureCollection を返す', () => {
    const fc = meshToFeatureCollection([MESH_RECORD]);
    expect(fc.type).toBe('FeatureCollection');
    expect(fc.features).toHaveLength(1);
  });

  it('Polygon としてリングを1段ネストして格納する', () => {
    const [feature] = meshToFeatureCollection([MESH_RECORD]).features;
    expect(feature.geometry.type).toBe('Polygon');
    // GeoJSON Polygon の coordinates は「リングの配列」
    expect(feature.geometry.coordinates).toHaveLength(1);
    expect(feature.geometry.coordinates[0][0]).toEqual([139.7125, 35.5208333]);
  });

  it('リングが閉じている（始点と終点が一致）', () => {
    const [feature] = meshToFeatureCollection([MESH_RECORD]).features;
    const ring = feature.geometry.coordinates[0];
    expect(ring[0]).toEqual(ring[ring.length - 1]);
  });

  it('開いた4点リングが渡されても閉じて返す', () => {
    const open = { ...MESH_RECORD, coordinates: MESH_RECORD.coordinates.slice(0, 4) };
    const [feature] = meshToFeatureCollection([open]).features;
    const ring = feature.geometry.coordinates[0];
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[4]);
  });

  it('populationDensity と meshId を properties に持つ', () => {
    const [feature] = meshToFeatureCollection([MESH_RECORD]).features;
    expect(feature.properties.populationDensity).toBe(20200);
    expect(feature.properties.meshId).toBe('53392537');
  });

  it('populationDensity が欠損なら 0 で補う', () => {
    const broken = { ...MESH_RECORD, populationDensity: undefined };
    const [feature] = meshToFeatureCollection([broken]).features;
    expect(feature.properties.populationDensity).toBe(0);
  });

  it('座標が不正なレコードは黙って除外し、他は残す', () => {
    const fc = meshToFeatureCollection([
      MESH_RECORD,
      { meshId: 'x', coordinates: null, populationDensity: 1 },
      { meshId: 'y', coordinates: [[1, 2]], populationDensity: 1 }, // 点が足りない
    ]);
    expect(fc.features).toHaveLength(1);
  });

  it('空配列・null でも落ちず空の FeatureCollection を返す', () => {
    expect(meshToFeatureCollection([]).features).toEqual([]);
    expect(meshToFeatureCollection(null).features).toEqual([]);
  });
});

describe('toiletsToFeatureCollection', () => {
  it('Point Feature に変換し、座標は [lng, lat] 順', () => {
    const [feature] = toiletsToFeatureCollection([TOILET_RECORD]).features;
    expect(feature.geometry.type).toBe('Point');
    expect(feature.geometry.coordinates).toEqual([139.377736, 35.5216013]);
  });

  it('属性を properties に平坦化する（レイヤーのフィルタで参照するため）', () => {
    const [feature] = toiletsToFeatureCollection([TOILET_RECORD]).features;
    expect(feature.properties.id).toBe('toilet_n10006253728');
    expect(feature.properties.name).toBe('公衆トイレ');
    expect(feature.properties.accessible).toBe(false);
    expect(feature.properties.ostomate).toBe(false);
    expect(feature.properties.open24h).toBe(false);
  });

  it('attributes が欠けていても false 埋めで落ちない', () => {
    const broken = { id: 't1', name: 'x', location: { lat: 35.5, lng: 139.7 } };
    const [feature] = toiletsToFeatureCollection([broken]).features;
    expect(feature.properties.accessible).toBe(false);
  });

  it('location が無いレコードは除外する', () => {
    const fc = toiletsToFeatureCollection([TOILET_RECORD, { id: 'ng', name: 'x' }]);
    expect(fc.features).toHaveLength(1);
  });

  it('空配列・null でも落ちない', () => {
    expect(toiletsToFeatureCollection([]).features).toEqual([]);
    expect(toiletsToFeatureCollection(undefined).features).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 避難所
// ---------------------------------------------------------------------------

import { sheltersToFeatureCollection } from '../src/geojson.js';

const SHELTER_RECORD = {
  id: 'evac_E1413000000001',
  name: '川崎中学校',
  address: '神奈川県横浜市中区日本大通1',
  location: { lat: 35.5231, lng: 139.7215 },
  isEmergencySite: true,
  isEvacuationCenter: true,
  isWelfareShelter: false,
  disasterTypes: ['flood', 'earthquake', 'fire'],
  targetOccupants: '',
};

describe('sheltersToFeatureCollection', () => {
  it('Point Feature に変換し、座標は [lng, lat] 順', () => {
    const [feature] = sheltersToFeatureCollection([SHELTER_RECORD]).features;
    expect(feature.geometry.type).toBe('Point');
    expect(feature.geometry.coordinates).toEqual([139.7215, 35.5231]);
  });

  it('2種類の役割を properties に持つ', () => {
    const [feature] = sheltersToFeatureCollection([SHELTER_RECORD]).features;
    expect(feature.properties.isEmergencySite).toBe(true);
    expect(feature.properties.isEvacuationCenter).toBe(true);
    expect(feature.properties.isWelfareShelter).toBe(false);
  });

  it('アイコン出し分け用に category を付与する', () => {
    const [feature] = sheltersToFeatureCollection([SHELTER_RECORD]).features;
    expect(feature.properties.category).toBe('both');
  });

  it('災害種別を disaster_* の真偽値に展開する（レイヤーfilter用）', () => {
    const [feature] = sheltersToFeatureCollection([SHELTER_RECORD]).features;
    expect(feature.properties.disaster_flood).toBe(true);
    expect(feature.properties.disaster_earthquake).toBe(true);
    expect(feature.properties.disaster_fire).toBe(true);
    // 非該当の種別は false で埋まる
    expect(feature.properties.disaster_tsunami).toBe(false);
    expect(feature.properties.disaster_volcano).toBe(false);
  });

  it('ポップアップ表示用に元の配列も保持する', () => {
    const [feature] = sheltersToFeatureCollection([SHELTER_RECORD]).features;
    expect(feature.properties.disasterTypes).toEqual(['flood', 'earthquake', 'fire']);
  });

  it('disasterTypes 欠損でも落ちない', () => {
    const broken = { ...SHELTER_RECORD, disasterTypes: undefined };
    const [feature] = sheltersToFeatureCollection([broken]).features;
    expect(feature.properties.disaster_flood).toBe(false);
    expect(feature.properties.disasterTypes).toEqual([]);
  });

  it('location が無いレコードは除外する', () => {
    const fc = sheltersToFeatureCollection([SHELTER_RECORD, { id: 'ng', name: 'x' }]);
    expect(fc.features).toHaveLength(1);
  });

  it('空配列・null でも落ちない', () => {
    expect(sheltersToFeatureCollection([]).features).toEqual([]);
    expect(sheltersToFeatureCollection(null).features).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 医療機関
// ---------------------------------------------------------------------------

import { hospitalsToFeatureCollection } from '../src/geojson.js';

const HOSPITAL_RECORD = {
  id: 'hosp_1411010000001',
  name: '川崎中央病院',
  address: '神奈川県川崎市川崎区1-1',
  location: { lat: 35.5231, lng: 139.7215 },
  isDisasterBase: false,
  capabilities: ['内科', '精神科'],
  topDiseases: [],
  website: 'https://example.com',
};

describe('hospitalsToFeatureCollection', () => {
  it('Point Feature に変換し、座標は [lng, lat] 順', () => {
    const [f] = hospitalsToFeatureCollection([HOSPITAL_RECORD]).features;
    expect(f.geometry.type).toBe('Point');
    expect(f.geometry.coordinates).toEqual([139.7215, 35.5231]);
  });

  it('診療科は配列のまま保持する（レイヤーの in 式で参照するため）', () => {
    const [f] = hospitalsToFeatureCollection([HOSPITAL_RECORD]).features;
    expect(f.properties.capabilities).toEqual(['内科', '精神科']);
  });

  it('capabilities 欠損でも配列になる', () => {
    const broken = { ...HOSPITAL_RECORD, capabilities: undefined };
    const [f] = hospitalsToFeatureCollection([broken]).features;
    expect(f.properties.capabilities).toEqual([]);
  });

  it('location が無いレコードは除外する', () => {
    const fc = hospitalsToFeatureCollection([HOSPITAL_RECORD, { id: 'ng' }]);
    expect(fc.features).toHaveLength(1);
  });

  it('空配列・null でも落ちない', () => {
    expect(hospitalsToFeatureCollection([]).features).toEqual([]);
    expect(hospitalsToFeatureCollection(null).features).toEqual([]);
  });
});
