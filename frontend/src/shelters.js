/**
 * 避難所の表示制御ロジック。
 *
 * 避難所は災害時の役割がまったく異なる2種類に分かれる。両者を混ぜて
 * 表示すると「津波のとき生活用の避難所へ逃げてしまう」ような誤解を招くため、
 * 地図上でも明確に区別する。
 *
 *   指定緊急避難場所 (isEmergencySite)   … 切迫した危険から「命を守る」場所。
 *                                          災害種別ごとに指定される。
 *   指定避難所       (isEvacuationCenter) … 自宅に戻れない人が「生活する」場所。
 *                                          災害種別の指定は無い。
 *
 * キーは `scripts/clean_shelters.py` の DISASTER_COLUMNS と一致させること。
 */

/** 災害種別のキー → 日本語ラベル（災害対策基本法の8種別）。 */
export const DISASTER_LABELS = {
  flood: '洪水',
  landslide: '崖崩れ・土石流・地滑り',
  stormSurge: '高潮',
  earthquake: '地震',
  tsunami: '津波',
  fire: '大規模な火事',
  inlandFlood: '内水氾濫',
  volcano: '火山現象',
};

/** 表示順を固定したキー一覧。 */
export const DISASTER_KEYS = Object.keys(DISASTER_LABELS);

/** 避難所の分類。アイコンの出し分けに使う。 */
export function shelterCategory(shelter) {
  const emergency = shelter && shelter.isEmergencySite === true;
  const center = shelter && shelter.isEvacuationCenter === true;
  if (emergency && center) return 'both';
  if (emergency) return 'emergency';
  if (center) return 'center';
  return 'unknown';
}

/**
 * 分類ごとにアイコンを出し分ける式。
 *
 *   both      … 兼用（赤丸ピン）
 *   emergency … 命を守る場所（赤ピン）
 *   center    … 生活する場所（青ピン）
 */
export function buildShelterIconExpression() {
  return [
    'case',
    ['==', ['get', 'category'], 'both'], 'pin-round-red',
    ['==', ['get', 'category'], 'emergency'], 'pin-red',
    ['==', ['get', 'category'], 'center'], 'pin-blue',
    'pin-darkblue',
  ];
}

/**
 * JS 側で避難所を絞り込む（件数表示・テスト用）。
 *
 * @param {Array} shelters
 * @param {{showEmergency: boolean, showCenter: boolean, disasterType?: string}} options
 */
export function filterShelters(shelters, options) {
  if (!Array.isArray(shelters)) return [];
  const { showEmergency, showCenter, disasterType } = options || {};

  return shelters.filter((s) => {
    if (!s) return false;

    const isEmergency = s.isEmergencySite === true;
    const isCenter = s.isEvacuationCenter === true;

    // 役割による絞り込み（兼用はどちらか一方がONなら残す）
    const roleMatches = (showEmergency && isEmergency) || (showCenter && isCenter);
    if (!roleMatches) return false;

    // 災害種別による絞り込み。
    // 「生活する場所」には災害種別の指定が無いため、種別を選ぶと残らない。
    if (disasterType) {
      const types = Array.isArray(s.disasterTypes) ? s.disasterTypes : [];
      if (!types.includes(disasterType)) return false;
    }

    return true;
  });
}

/**
 * Azure Maps のレイヤーに渡すフィルタ式を返す。
 *
 * DataSource を作り直さずレイヤーの filter だけ差し替えることで、
 * 4,265件のピンを再生成せずに表示を切り替えられる。
 *
 * @param {{showEmergency: boolean, showCenter: boolean, disasterType?: string}} options
 * @returns {Array|null} 絞り込み不要なら null
 */
export function buildShelterFilterExpression(options) {
  const { showEmergency, showCenter, disasterType } = options || {};

  // どちらもOFF → 何も表示しない（常に偽になる式）
  if (!showEmergency && !showCenter) {
    return ['==', ['literal', true], ['literal', false]];
  }

  const conditions = [];

  if (showEmergency && !showCenter) {
    conditions.push(['==', ['get', 'isEmergencySite'], true]);
  } else if (!showEmergency && showCenter) {
    conditions.push(['==', ['get', 'isEvacuationCenter'], true]);
  }

  if (disasterType) {
    // 配列プロパティへの 'in' 演算子は SDK バージョン差が出るため、
    // GeoJSON 生成時に真偽値へ展開した disaster_* を参照する
    conditions.push(['==', ['get', `disaster_${disasterType}`], true]);
  }

  if (conditions.length === 0) return null;
  if (conditions.length === 1) return conditions[0];
  return ['all', ...conditions];
}
