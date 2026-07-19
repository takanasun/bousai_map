/**
 * トイレの絞り込みロジック。
 *
 * 「多機能トイレ」の定義はバックエンド `src/services/infra.py` の
 * `filter_multifunction_toilets` と統一する:
 *   車椅子対応 (accessible) **または** オストメイト対応 (ostomate)
 *
 * 定義を変える場合は必ず両方を揃えること。片方だけ変えると、
 * 同じチェックボックスでもサーバ経由とクライアント絞り込みで件数が食い違う。
 */

/**
 * 多機能トイレかどうかを判定する。
 * @param {{attributes?: {accessible?: boolean, ostomate?: boolean}}} toilet
 * @returns {boolean}
 */
export function isMultifunctionToilet(toilet) {
  const attributes = toilet && toilet.attributes;
  if (!attributes) return false;
  return attributes.accessible === true || attributes.ostomate === true;
}

/**
 * @param {Array} toilets トイレの配列
 * @param {boolean} multifunctionOnly true なら多機能トイレのみに絞る
 * @returns {Array} 絞り込み後の新しい配列（入力は破壊しない）
 */
export function filterToilets(toilets, multifunctionOnly) {
  if (!Array.isArray(toilets)) return [];
  if (!multifunctionOnly) return [...toilets];
  return toilets.filter(isMultifunctionToilet);
}

/**
 * Azure Maps のレイヤーに渡すフィルタ式を返す。
 *
 * DataSource を作り直さずレイヤーの filter だけ差し替えることで、
 * 1953件のピンを再生成せずに表示を切り替えられる（仕様 7.1 のパフォーマンス要件）。
 *
 * @param {boolean} multifunctionOnly
 * @returns {Array|null} 絞り込み不要なら null
 */
export function buildToiletFilterExpression(multifunctionOnly) {
  if (!multifunctionOnly) return null;
  return [
    'any',
    ['==', ['get', 'accessible'], true],
    ['==', ['get', 'ostomate'], true],
  ];
}
