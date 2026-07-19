/**
 * 検索条件パネル（ハンバーガーメニュー）の開閉ロジック。
 *
 * ヘッダに条件を全部並べると、スマホでは条件だけで画面が埋まり
 * 地図がほとんど見えない。タイトルだけ残して条件は左からの引き出しに収める。
 *
 * DOM 操作は main.js が持ち、ここは状態遷移だけを扱う（テスト可能にするため）。
 */

/**
 * 引き出しが地図に重ならなくなる画面幅(px)。
 * `style.css` の `@media (min-width: 768px)` と一致させること。
 * ずれると「閉じたのに地図が見えない」「開いたまま操作できない」が起きる。
 */
export const DRAWER_BREAKPOINT_PX = 768;

/**
 * 引き出しの開閉状態を反転する。
 *
 * `aria-expanded` は属性なので文字列で返り、未設定なら null になる。
 * どちらも「閉じている」とみなして、1回目の押下で必ず開くようにする。
 *
 * @param {boolean|string|null|undefined} current 現在の開閉状態
 * @returns {boolean} 次の開閉状態
 */
export function nextDrawerState(current) {
  const isOpen = current === true || current === 'true';
  return !isOpen;
}

/**
 * 条件を変更したあと引き出しを閉じるべきか。
 *
 * 狭い画面では引き出しが地図に重なるため、閉じて結果を見せる。
 * 広い画面では地図と並ぶので開いたままにする（条件を続けて変えるとき、
 * そのたびに閉じられると操作しづらい）。
 *
 * @param {number} viewportWidth 画面幅(px)
 * @returns {boolean}
 */
export function shouldCloseOnSelection(viewportWidth) {
  // 幅が取れない環境では閉じない。誤って閉じるより開いたままのほうが実害が小さい
  if (!Number.isFinite(viewportWidth)) return false;
  return viewportWidth < DRAWER_BREAKPOINT_PX;
}
