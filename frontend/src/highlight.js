/**
 * AIの回答に登場した施設を地図上で強調する。
 *
 * AIに施設IDを列挙させると存在しないIDを作る恐れがあるため、
 * 「サーバがAIに実際に渡した候補」の名前が回答文に現れたかで判定する。
 * 候補はサーバ側の検索結果なので、実在しない施設が混ざることはない。
 */

/** 強調用の旗。既存のピン（青丸/赤/濃紺）と重ならない色を使う。 */
export const HIGHLIGHT_ICONS = {
  shelter: '🚩',
  hospital: '🏥',
  toilet: '🚻',
};

/** 種別ごとの旗の色（既存レイヤーで未使用の色）。 */
export const HIGHLIGHT_COLORS = {
  shelter: '#f9a825', // 黄
  hospital: '#00897b', // 緑
  toilet: '#8e24aa', // 紫
};

/** 既定名など、施設を特定できない一般的な名称。名前照合から除外する。 */
const GENERIC_NAMES = new Set(['公衆トイレ', '避難所', '医療機関', 'トイレ', '病院']);

/**
 * 回答文に距離が書かれているか。
 *
 * トイレは96%が既定名「公衆トイレ」で、AIは名前を書かず
 * 「距離: 0.117km」とだけ答えることがある。名前照合だけでは検出できない。
 * 「0.42」と「0.420」のような末尾0の差も吸収する。
 */
function mentionsDistance(answer, distanceKm) {
  const value = Number(distanceKm);
  if (!Number.isFinite(value)) return false;

  // 小数3桁までの表記ゆれを許容し、数値として一致するものだけ拾う。
  // 「半径2km」のような無関係な数値を拾わないよう、km 単位の記述に限定する。
  const pattern = /(\d+(?:\.\d+)?)\s*km/gi;
  let match = pattern.exec(answer);
  while (match !== null) {
    if (Math.abs(Number(match[1]) - value) < 0.0005) return true;
    match = pattern.exec(answer);
  }
  return false;
}

/**
 * 回答文で言及された候補を返す。
 *
 * 名前で照合し、名前が一般的すぎて特定できない場合は距離で照合する。
 *
 * @param {string} answer AIの回答テキスト
 * @param {Array} candidates サーバが返した候補（AIに渡した施設）
 * @returns {Array} 言及された候補（重複なし・座標を持つものだけ）
 */
export function matchMentionedFacilities(answer, candidates) {
  if (!answer || !Array.isArray(candidates)) return [];

  const seen = new Set();
  const hits = [];

  for (const candidate of candidates) {
    if (!candidate) continue;
    if (seen.has(candidate.id)) continue;

    // 座標が無いと地図に置けない
    const loc = candidate.location;
    if (!loc || !Number.isFinite(Number(loc.lat)) || !Number.isFinite(Number(loc.lng))) continue;

    const name = (candidate.name || '').trim();
    // 空文字はどんな文にも含まれてしまうため除外する。
    // 既定名は多数の施設で共通なので、名前だけでは特定できない。
    const nameIdentifies = Boolean(name) && !GENERIC_NAMES.has(name);

    const matched = nameIdentifies
      ? answer.includes(name)
      : mentionsDistance(answer, candidate.distanceKm);

    if (matched) {
      seen.add(candidate.id);
      hits.push(candidate);
    }
  }

  return hits;
}

/** 強調対象を GeoJSON に変換する。 */
export function highlightsToFeatureCollection(facilities) {
  if (!Array.isArray(facilities)) {
    return { type: 'FeatureCollection', features: [] };
  }

  const features = [];
  for (const f of facilities) {
    const loc = f && f.location;
    if (!loc) continue;
    const lat = Number(loc.lat);
    const lng = Number(loc.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;

    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lng, lat] },
      properties: {
        id: f.id,
        name: f.name,
        kind: f.kind,
        distanceKm: f.distanceKm,
      },
    });
  }

  return { type: 'FeatureCollection', features };
}
