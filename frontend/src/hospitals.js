/**
 * 医療機関の表示制御ロジック。
 *
 * トイレ（青丸ピン）・避難所（赤/青ピン）と一目で区別できるよう、
 * 医療機関は専用アイコン + 赤十字ラベルで描画する。
 *
 * 診療科は `scripts/clean_hospitals.py` が厚労省データの「診療科目名」を
 * そのまま配列に畳んだもの（内科・精神科・整形外科 …）。
 */

/**
 * 医療機関のアイコン画像ID。`map.imageSprite.add()` で自前登録する。
 *
 * 以前は組み込みの 'pin-round-darkblue' に文字の ✚ を重ねていたが、
 * このピンは中央が白丸で、重ねる文字は em 単位のオフセット指定しかできず
 * 白丸の中に十字が収まらなかった（ただの紺色の丸に見えていた）。
 * 図形として描けば中心は座標で保証できるため、SVG を自前で用意している。
 */
export const HOSPITAL_ICON = 'hospital-cross';

/** アイコンの色。操作パネルの凡例チップ(.pin-hospital)と同じ濃紺。 */
export const HOSPITAL_COLOR = '#1a4d8f';

/** アイコンの一辺(px)。地図側で `size` を掛けて縮小する。 */
const ICON_SIZE = 32;

/**
 * 「濃紺の◯に白い十字」のアイコンを SVG の data URI で返す。
 *
 * 十字は円の中心（cx, cy）を基準に組み立てるため、サイズを変えても
 * 中心からずれない。凡例チップと同じ絵柄にして意味を揃えている。
 *
 * @returns {string} `map.imageSprite.add()` に渡せる data URI
 */
export function hospitalIconDataUri() {
  const size = ICON_SIZE;
  const c = size / 2;              // 円の中心
  const r = c - 1;                 // 縁が切れないよう1px内側に置く
  const arm = r * 0.62;            // 十字の腕の長さ（半径に対する比）
  const thickness = r * 0.26;      // 十字の太さ

  // 縦棒と横棒。いずれも中心 (c, c) を基準に置く
  const bars = [
    { x: c - thickness / 2, y: c - arm, width: thickness, height: arm * 2 },
    { x: c - arm, y: c - thickness / 2, width: arm * 2, height: thickness },
  ]
    .map(
      (b) =>
        `<rect x="${b.x}" y="${b.y}" width="${b.width}" height="${b.height}" fill="#ffffff"/>`,
    )
    .join('');

  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" ` +
    `viewBox="0 0 ${size} ${size}">` +
    `<circle cx="${c}" cy="${c}" r="${r}" fill="${HOSPITAL_COLOR}" ` +
    `stroke="#ffffff" stroke-width="1.5"/>` +
    bars +
    '</svg>';

  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** 診療科の選択肢に並べる既定の件数。 */
export const DEFAULT_SPECIALITY_LIMIT = 20;

/**
 * 読み込んだ医療機関から診療科の一覧を集計する。
 *
 * 選択肢をハードコードせず実データから作ることで、データを差し替えても
 * 存在しない診療科が UI に出ない（メッシュ解像度と同じ考え方）。
 *
 * @param {Array} hospitals
 * @param {number} [limit] 返す件数の上限（出現回数の多い順）
 * @returns {string[]} 出現回数の多い順に並べた診療科名
 */
export function collectSpecialities(hospitals, limit = DEFAULT_SPECIALITY_LIMIT) {
  if (!Array.isArray(hospitals)) return [];

  const counts = new Map();
  for (const hospital of hospitals) {
    const capabilities = (hospital && hospital.capabilities) || [];
    for (const name of capabilities) {
      if (!name) continue;
      counts.set(name, (counts.get(name) || 0) + 1);
    }
  }

  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'ja'))
    .slice(0, limit)
    .map(([name]) => name);
}

/** ポップアップで診療科を折り返す件数。 */
export const SPECIALITIES_PER_LINE = 3;

/**
 * 診療科を n 件ずつの行に区切る。
 *
 * 大きな病院は診療科が20を超えることがあり、1行に並べると
 * ポップアップが横に伸びて読めなくなるため折り返す。
 *
 * @param {string[]} capabilities
 * @param {number} [perLine] 1行あたりの件数（0以下は全件1行として扱う）
 * @returns {string[]} 「内科、外科、皮膚科」のように結合済みの行
 */
export function formatSpecialityLines(capabilities, perLine = SPECIALITIES_PER_LINE) {
  if (!Array.isArray(capabilities)) return [];

  const names = capabilities.filter((name) => name);
  if (names.length === 0) return [];

  // perLine が 0 以下だと while が進まないため、全件1行に丸める
  const step = perLine > 0 ? perLine : names.length;

  const lines = [];
  for (let i = 0; i < names.length; i += step) {
    lines.push(names.slice(i, i + step).join('、'));
  }
  return lines;
}

/** 指定の診療科を持つか。 */
export function hasSpeciality(hospital, speciality) {
  const capabilities = (hospital && hospital.capabilities) || [];
  return capabilities.includes(speciality);
}

/**
 * JS 側で医療機関を絞り込む（件数表示・テスト用）。
 *
 * @param {Array} hospitals
 * @param {string} speciality 空文字なら絞り込みなし
 * @returns {Array} 新しい配列（入力は破壊しない）
 */
export function filterHospitals(hospitals, speciality) {
  if (!Array.isArray(hospitals)) return [];
  const needle = (speciality || '').trim();
  if (!needle) return [...hospitals];
  return hospitals.filter((h) => hasSpeciality(h, needle));
}

/**
 * Azure Maps のレイヤーに渡すフィルタ式を返す。
 *
 * DataSource を作り直さずレイヤーの filter だけ差し替えることで、
 * 310件のピンを再生成せずに表示を切り替えられる。
 *
 * 診療科は配列プロパティなので `in` 演算子で判定する
 * （避難所の災害種別は真偽値へ展開したが、こちらは選択肢が20種以上あり
 *  プロパティ数が増えすぎるため配列のまま扱う）。
 *
 * @param {string} speciality
 * @returns {Array|null} 絞り込み不要なら null
 */
export function buildHospitalFilterExpression(speciality) {
  const needle = (speciality || '').trim();
  if (!needle) return null;
  return ['in', needle, ['get', 'capabilities']];
}
