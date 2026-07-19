/**
 * 検索条件パネル（ハンバーガーメニュー）の開閉ロジックのテスト。
 *
 * ヘッダに条件を全部並べると、スマホでは地図がほとんど見えなくなる。
 * タイトルだけ残して条件は引き出しに収め、必要なときだけ開く。
 *
 * DOM 操作は main.js に隔離し、ここでは状態遷移だけを検証する
 * （vitest の環境は node のため。既存の about.js と同じ方針）。
 */

import { describe, it, expect } from 'vitest';
import {
  nextDrawerState,
  shouldCloseOnSelection,
  DRAWER_BREAKPOINT_PX,
} from '../src/drawer.js';

describe('nextDrawerState', () => {
  it('閉じているときは開く', () => {
    expect(nextDrawerState(false)).toBe(true);
  });

  it('開いているときは閉じる', () => {
    expect(nextDrawerState(true)).toBe(false);
  });

  it('未設定は閉じている扱いにして開く', () => {
    // aria-expanded が未設定の初期状態でも1回目の押下で開くこと
    expect(nextDrawerState(null)).toBe(true);
    expect(nextDrawerState(undefined)).toBe(true);
  });

  it('文字列の "true" / "false" も解釈する', () => {
    // aria-expanded は属性なので文字列で返ってくる
    expect(nextDrawerState('true')).toBe(false);
    expect(nextDrawerState('false')).toBe(true);
  });
});

describe('shouldCloseOnSelection', () => {
  // 狭い画面では引き出しが地図に重なるため、条件を変えたら閉じて
  // 結果を見せる。広い画面では地図と並ぶので開いたままにする
  // （連続して条件を変えるたびに閉じると操作しづらい）。
  it('狭い画面では閉じる', () => {
    expect(shouldCloseOnSelection(DRAWER_BREAKPOINT_PX - 1)).toBe(true);
  });

  it('広い画面では閉じない', () => {
    expect(shouldCloseOnSelection(DRAWER_BREAKPOINT_PX + 1)).toBe(false);
  });

  it('境界値は広い画面として扱う', () => {
    expect(shouldCloseOnSelection(DRAWER_BREAKPOINT_PX)).toBe(false);
  });

  it('幅が取れない場合は閉じない（誤って閉じるより安全）', () => {
    expect(shouldCloseOnSelection(undefined)).toBe(false);
    expect(shouldCloseOnSelection(null)).toBe(false);
    expect(shouldCloseOnSelection(NaN)).toBe(false);
  });
});

describe('DRAWER_BREAKPOINT_PX', () => {
  it('CSS のメディアクエリと揃える必要があるため定数で持つ', () => {
    // style.css の @media (min-width: ...) と一致させること。
    // ずれると「閉じるのに地図が見えない」状態が起きる。
    expect(DRAWER_BREAKPOINT_PX).toBe(768);
  });
});
