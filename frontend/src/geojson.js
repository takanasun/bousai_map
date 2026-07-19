/**
 * API レスポンス → GeoJSON FeatureCollection への変換。
 *
 * Azure Maps の DataSource は GeoJSON をそのまま受け取れるため、
 * ここで変換しておけば描画側（main.js）は薄く保てる。
 */

import { DISASTER_KEYS, shelterCategory } from './shelters.js';

function emptyCollection() {
  return { type: 'FeatureCollection', features: [] };
}

/**
 * リングが閉じていなければ閉じる（GeoJSON Polygon の要件）。
 * `scripts/clean_mesh.py` は5点の閉じたリングを出力するが、
 * 将来データ形式が変わっても壊れないようにしておく。
 */
function closeRing(ring) {
  const first = ring[0];
  const last = ring[ring.length - 1];
  if (first[0] === last[0] && first[1] === last[1]) return ring;
  return [...ring, [first[0], first[1]]];
}

function isValidRing(coordinates) {
  return (
    Array.isArray(coordinates) &&
    coordinates.length >= 4 &&
    coordinates.every(
      (p) => Array.isArray(p) && p.length >= 2 && Number.isFinite(Number(p[0])) && Number.isFinite(Number(p[1])),
    )
  );
}

/** location: {lat, lng} を [lng, lat] に変換する。不正なら null。 */
function toPosition(location) {
  if (!location) return null;
  const lat = Number(location.lat);
  const lng = Number(location.lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return [lng, lat];
}

/**
 * 人口メッシュ（docs/spec.md 4.1）を Polygon の FeatureCollection に変換する。
 * @param {Array} records /api/mesh の items
 */
export function meshToFeatureCollection(records) {
  if (!Array.isArray(records)) return emptyCollection();

  const features = [];
  for (const record of records) {
    if (!record || !isValidRing(record.coordinates)) continue;

    const density = Number(record.populationDensity);
    features.push({
      type: 'Feature',
      geometry: {
        type: 'Polygon',
        coordinates: [closeRing(record.coordinates)],
      },
      properties: {
        meshId: record.meshId,
        populationDensity: Number.isFinite(density) ? density : 0,
      },
    });
  }

  return { type: 'FeatureCollection', features };
}

/**
 * 公衆トイレ（docs/spec.md 4.4）を Point の FeatureCollection に変換する。
 * 属性はレイヤーのフィルタ式から参照できるよう properties に平坦化する。
 * @param {Array} records /api/toilets の items
 */
export function toiletsToFeatureCollection(records) {
  if (!Array.isArray(records)) return emptyCollection();

  const features = [];
  for (const record of records) {
    const position = toPosition(record && record.location);
    if (!position) continue;

    const attributes = record.attributes || {};
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: position },
      properties: {
        id: record.id,
        name: record.name || '公衆トイレ',
        accessible: attributes.accessible === true,
        ostomate: attributes.ostomate === true,
        open24h: attributes.open24h === true,
      },
    });
  }

  return { type: 'FeatureCollection', features };
}

/**
 * 医療機関（docs/spec.md 4.2）を Point の FeatureCollection に変換する。
 *
 * 診療科(capabilities)は配列のまま properties に持たせ、レイヤーの
 * filter 式（`['in', 科名, ['get','capabilities']]`）から参照する。
 *
 * @param {Array} records /api/hospitals の items
 */
export function hospitalsToFeatureCollection(records) {
  if (!Array.isArray(records)) return emptyCollection();

  const features = [];
  for (const record of records) {
    const position = toPosition(record && record.location);
    if (!position) continue;

    const capabilities = Array.isArray(record.capabilities) ? record.capabilities : [];

    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: position },
      properties: {
        id: record.id,
        name: record.name || '医療機関',
        address: record.address || '',
        isDisasterBase: record.isDisasterBase === true,
        capabilities,
        topDiseases: Array.isArray(record.topDiseases) ? record.topDiseases : [],
        website: record.website || '',
      },
    });
  }

  return { type: 'FeatureCollection', features };
}

/**
 * 避難所（docs/spec.md 4.3）を Point の FeatureCollection に変換する。
 *
 * 災害種別はレイヤーの filter から参照できるよう `disaster_<key>` の真偽値に
 * 展開する（配列プロパティへの 'in' 演算子は SDK バージョン差が出るため）。
 * 元の配列はポップアップ表示用に残す。
 *
 * @param {Array} records /api/evacuation の items
 */
export function sheltersToFeatureCollection(records) {
  if (!Array.isArray(records)) return emptyCollection();

  const features = [];
  for (const record of records) {
    const position = toPosition(record && record.location);
    if (!position) continue;

    const disasterTypes = Array.isArray(record.disasterTypes) ? record.disasterTypes : [];

    const properties = {
      id: record.id,
      name: record.name || '避難所',
      address: record.address || '',
      isEmergencySite: record.isEmergencySite === true,
      isEvacuationCenter: record.isEvacuationCenter === true,
      isWelfareShelter: record.isWelfareShelter === true,
      targetOccupants: record.targetOccupants || '',
      category: shelterCategory(record),
      disasterTypes,
    };

    // 災害種別を真偽値へ展開（非該当も false で埋め、filter が確実に効くようにする）
    for (const key of DISASTER_KEYS) {
      properties[`disaster_${key}`] = disasterTypes.includes(key);
    }

    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: position },
      properties,
    });
  }

  return { type: 'FeatureCollection', features };
}
