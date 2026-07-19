import { describe, it, expect } from 'vitest';
import { ABOUT_PARAGRAPHS, ABOUT_TITLE, nextAboutState } from '../src/about.js';

// 企画意図の吹き出し。文面は作者本人の言葉なので、
// 表示側の都合で書き換わっていないことをテストで固定する。

describe('ABOUT_PARAGRAPHS', () => {
  it('段落に分かれている（1文の塊にしない）', () => {
    expect(ABOUT_PARAGRAPHS.length).toBeGreaterThan(1);
  });

  it('空の段落を含まない', () => {
    for (const paragraph of ABOUT_PARAGRAPHS) {
      expect(paragraph.trim().length).toBeGreaterThan(0);
    }
  });

  it('4つの機能を追加した動機がすべて残っている', () => {
    const text = ABOUT_PARAGRAPHS.join('');
    expect(text).toContain('避難所が混むかどうか');  // 人口メッシュ
    expect(text).toContain('災害時に頼れる病院');    // 医療機関
    expect(text).toContain('近くのトイレ');          // トイレ
    expect(text).toContain('地価が安いところ');      // 地価
  });

  it('原文の言い回しを保っている', () => {
    const text = ABOUT_PARAGRAPHS.join('');
    expect(text).toContain('このマップの企画意図ですが');
    expect(text).toContain('機能を設計いたしました');
  });

  // 一般公開するページなので、家族が特定される情報は載せない。
  // 住所を混入させた過去があるため、テストで戻せないようにしておく。
  it('家族の続柄や病名を含まない', () => {
    const text = ABOUT_PARAGRAPHS.join('');
    for (const sensitive of ['息子', '娘', '妻', '夫', '喘息', 'ぜんそく']) {
      expect(text).not.toContain(sensitive);
    }
  });

  it('医療機関の動機は一般化した表現で残っている', () => {
    expect(ABOUT_PARAGRAPHS.join('')).toContain('家族が疾患をもっている');
  });
});

describe('ABOUT_TITLE', () => {
  it('見出しがある', () => {
    expect(ABOUT_TITLE.trim().length).toBeGreaterThan(0);
  });
});

describe('nextAboutState', () => {
  it('閉じているときは開く', () => {
    expect(nextAboutState(false)).toBe(true);
  });

  it('開いているときは閉じる', () => {
    expect(nextAboutState(true)).toBe(false);
  });

  it('未定義は閉じている扱いにして開く', () => {
    // aria-expanded が未設定の初期状態でも1回目の押下で開くこと
    expect(nextAboutState(undefined)).toBe(true);
    expect(nextAboutState(null)).toBe(true);
  });

  it('文字列の "true" / "false" も解釈する', () => {
    // aria-expanded は属性なので文字列で返ってくる
    expect(nextAboutState('true')).toBe(false);
    expect(nextAboutState('false')).toBe(true);
  });
});
