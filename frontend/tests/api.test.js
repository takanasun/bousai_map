/**
 * API 通信層のテスト。
 *
 * 実際のネットワークは使わず global.fetch をモックする。
 * 仕様 7.1 に従い、失敗時は画面を白くせずユーザ向けメッセージを投げること。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  fetchMapConfig,
  fetchMesh,
  fetchToilets,
  fetchHospitals,
  postChat,
  resolveApiBase,
  ApiError,
} from '../src/api.js';

function mockFetchOnce(body, { ok = true, status = 200 } = {}) {
  global.fetch = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
  });
}

beforeEach(() => {
  global.fetch = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('fetchMapConfig', () => {
  it('/api/config を呼び、キーを返す', async () => {
    mockFetchOnce({ azureMapsKey: 'k', configured: true });
    const cfg = await fetchMapConfig();
    expect(global.fetch.mock.calls[0][0]).toBe('/api/config');
    expect(cfg.azureMapsKey).toBe('k');
    expect(cfg.configured).toBe(true);
  });
});

describe('fetchMesh', () => {
  it('/api/mesh を呼び、items と解像度情報を返す', async () => {
    mockFetchOnce({
      count: 2,
      resolution: '1km',
      availableResolutions: ['1km', '500m'],
      items: [{ meshId: '1' }, { meshId: '2' }],
    });
    const result = await fetchMesh();
    expect(global.fetch.mock.calls[0][0]).toBe('/api/mesh');
    expect(result.items).toHaveLength(2);
    expect(result.resolution).toBe('1km');
    expect(result.availableResolutions).toEqual(['1km', '500m']);
  });

  it('解像度を指定するとクエリに載る', async () => {
    mockFetchOnce({ count: 0, resolution: '500m', availableResolutions: ['1km', '500m'], items: [] });
    await fetchMesh('500m');
    expect(global.fetch.mock.calls[0][0]).toBe('/api/mesh?resolution=500m');
  });

  it('items が無い応答でも空配列を返す', async () => {
    mockFetchOnce({ count: 0 });
    const result = await fetchMesh();
    expect(result.items).toEqual([]);
    expect(result.availableResolutions).toEqual([]);
  });
});

describe('fetchToilets', () => {
  it('/api/toilets を呼ぶ', async () => {
    mockFetchOnce({ count: 1, items: [{ id: 't' }] });
    const items = await fetchToilets();
    expect(global.fetch.mock.calls[0][0]).toBe('/api/toilets');
    expect(items).toHaveLength(1);
  });
});

describe('resolveApiBase', () => {
  it('location が無ければ同一オリジン（空文字）', () => {
    expect(resolveApiBase(null)).toBe('');
  });

  it('Functions と同じオリジン(7071)で配信されていれば同一オリジン', () => {
    expect(resolveApiBase({ port: '7071', hostname: 'localhost' })).toBe('');
  });

  it('静的サーバー(5173)から開いた場合は Functions のオリジンを指す', () => {
    expect(resolveApiBase({ port: '5173', hostname: 'localhost' })).toBe(
      'http://localhost:7071',
    );
  });

  it('本番（ポート指定なし）では同一オリジン', () => {
    expect(resolveApiBase({ port: '', hostname: 'example.azurestaticapps.net' })).toBe('');
  });

  // 本番は SWA(静的) と Function App(API) がオリジンの異なる別リソースになるため、
  // index.html の <meta name="api-base"> で API 側を指す。
  describe('明示的に指定された基底URL', () => {
    it('指定があればそれを使う', () => {
      expect(
        resolveApiBase(
          { port: '', hostname: 'example.azurestaticapps.net' },
          'https://bousai-api.azurewebsites.net',
        ),
      ).toBe('https://bousai-api.azurewebsites.net');
    });

    it('末尾のスラッシュを取り除く（// になるのを防ぐ）', () => {
      expect(resolveApiBase({ port: '', hostname: 'x' }, 'https://api.example.com/')).toBe(
        'https://api.example.com',
      );
    });

    it('未置換のプレースホルダは無視する', () => {
      // デプロイ時の差し込みを忘れても、同一オリジンへフォールバックして
      // 「__API_BASE__/api/config」のような無意味なURLを叩かない
      expect(resolveApiBase({ port: '', hostname: 'x' }, '__API_BASE__')).toBe('');
    });

    it('空文字や空白のみなら無指定として扱う', () => {
      expect(resolveApiBase({ port: '', hostname: 'x' }, '   ')).toBe('');
      expect(resolveApiBase({ port: '', hostname: 'x' }, '')).toBe('');
    });

    it('ローカル開発の判定より優先される', () => {
      expect(
        resolveApiBase({ port: '5173', hostname: 'localhost' }, 'https://api.example.com'),
      ).toBe('https://api.example.com');
    });
  });
});

describe('エラーハンドリング', () => {
  it('HTTP エラー時は ApiError を投げる', async () => {
    mockFetchOnce({ error: 'データが取得できませんでした' }, { ok: false, status: 502 });
    await expect(fetchMesh()).rejects.toBeInstanceOf(ApiError);
  });

  it('サーバのエラーメッセージをそのまま利用者向けに伝える', async () => {
    mockFetchOnce({ error: 'データが取得できませんでした' }, { ok: false, status: 502 });
    await expect(fetchMesh()).rejects.toThrow('データが取得できませんでした');
  });

  it('通信自体が失敗した場合も ApiError にまとめる', async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(fetchMesh()).rejects.toBeInstanceOf(ApiError);
  });

  it('ApiError は status を保持する', async () => {
    mockFetchOnce({ error: 'x' }, { ok: false, status: 500 });
    await expect(fetchMesh()).rejects.toMatchObject({ status: 500 });
  });
});

describe('fetchHospitals', () => {
  it('/api/hospitals を呼ぶ', async () => {
    mockFetchOnce({ count: 1, items: [{ id: 'h1' }] });
    const items = await fetchHospitals();
    expect(global.fetch.mock.calls[0][0]).toBe('/api/hospitals');
    expect(items).toHaveLength(1);
  });
});

describe('postChat', () => {
  it('/api/chat へ質問と基準座標を POST する', async () => {
    mockFetchOnce({ answer: '最寄りは川崎中学校です', sources: { shelters: 3 } });
    const result = await postChat('一番近い避難所は？', { lat: 35.5, lng: 139.7 });

    const [url, init] = global.fetch.mock.calls[0];
    expect(url).toBe('/api/chat');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({
      question: '一番近い避難所は？',
      lat: 35.5,
      lng: 139.7,
    });
    expect(result.answer).toBe('最寄りは川崎中学校です');
    expect(result.sources.shelters).toBe(3);
  });

  it('AI未設定(503)はサーバのメッセージをそのまま伝える', async () => {
    mockFetchOnce({ error: 'AIアシスタントが未設定です。' }, { ok: false, status: 503 });
    await expect(postChat('質問', { lat: 35.5, lng: 139.7 })).rejects.toThrow('AIアシスタントが未設定です。');
  });

  it('応答が欠けていても空文字で返す', async () => {
    mockFetchOnce({});
    const result = await postChat('質問', { lat: 35.5, lng: 139.7 });
    expect(result.answer).toBe('');
  });
});
