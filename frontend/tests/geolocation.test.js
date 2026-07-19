/**
 * 現在地取得と「基準地点」の管理ロジックのテスト。
 *
 * ブラウザの navigator.geolocation は注入して差し替える（実際には呼ばない）。
 */

import { describe, it, expect, vi } from 'vitest';
import {
  GEOLOCATION_MESSAGES,
  getCurrentPosition,
  createReferencePoint,
} from '../src/geolocation.js';
import { DEFAULT_CENTER } from '../src/mapConfig.js';

function fakeGeolocation({ position, error }) {
  return {
    getCurrentPosition: vi.fn((onSuccess, onError) => {
      if (error) onError(error);
      else onSuccess(position);
    }),
  };
}

describe('getCurrentPosition', () => {
  it('成功時は lat/lng/accuracy を返す', async () => {
    const geo = fakeGeolocation({
      position: { coords: { latitude: 35.5, longitude: 139.7, accuracy: 12 } },
    });
    await expect(getCurrentPosition(geo)).resolves.toEqual({
      lat: 35.5,
      lng: 139.7,
      accuracy: 12,
    });
  });

  it('geolocation が使えない環境では拒否される', async () => {
    await expect(getCurrentPosition(null)).rejects.toThrow(
      GEOLOCATION_MESSAGES.UNSUPPORTED,
    );
  });

  it('許可拒否は利用者向けの日本語メッセージになる', async () => {
    const geo = fakeGeolocation({ error: { code: 1 } }); // PERMISSION_DENIED
    await expect(getCurrentPosition(geo)).rejects.toThrow(
      GEOLOCATION_MESSAGES.PERMISSION_DENIED,
    );
  });

  it('位置取得不能・タイムアウトもメッセージ化する', async () => {
    await expect(getCurrentPosition(fakeGeolocation({ error: { code: 2 } }))).rejects.toThrow(
      GEOLOCATION_MESSAGES.POSITION_UNAVAILABLE,
    );
    await expect(getCurrentPosition(fakeGeolocation({ error: { code: 3 } }))).rejects.toThrow(
      GEOLOCATION_MESSAGES.TIMEOUT,
    );
  });

  it('未知のエラーコードでも落ちない', async () => {
    await expect(getCurrentPosition(fakeGeolocation({ error: { code: 99 } }))).rejects.toThrow();
  });

  it('高精度・タイムアウトのオプションを渡す', async () => {
    const geo = fakeGeolocation({
      position: { coords: { latitude: 1, longitude: 2, accuracy: 3 } },
    });
    await getCurrentPosition(geo);
    const options = geo.getCurrentPosition.mock.calls[0][2];
    expect(options.enableHighAccuracy).toBe(true);
    expect(options.timeout).toBeGreaterThan(0);
  });
});

describe('createReferencePoint', () => {
  it('初期値は既定地点（神奈川県庁）', () => {
    const ref = createReferencePoint();
    expect(ref.get()).toEqual({ lat: DEFAULT_CENTER.lat, lng: DEFAULT_CENTER.lng });
    expect(ref.isDefault()).toBe(true);
  });

  it('現在地に更新できる（AIの質問と距離計算の基準が切り替わる）', () => {
    const ref = createReferencePoint();
    ref.set({ lat: 35.6, lng: 139.8 }, 'current');
    expect(ref.get()).toEqual({ lat: 35.6, lng: 139.8 });
    expect(ref.isDefault()).toBe(false);
    expect(ref.source()).toBe('current');
  });

  it('住所指定でも基準を切り替えられる', () => {
    const ref = createReferencePoint();
    ref.set({ lat: 35.4, lng: 139.6 }, 'address');
    expect(ref.source()).toBe('address');
    expect(ref.isDefault()).toBe(false);
  });

  it('既定地点に戻せる', () => {
    const ref = createReferencePoint();
    ref.set({ lat: 35.6, lng: 139.8 }, 'current');
    ref.reset();
    expect(ref.get()).toEqual({ lat: DEFAULT_CENTER.lat, lng: DEFAULT_CENTER.lng });
    expect(ref.isDefault()).toBe(true);
  });

  it('変更を購読できる', () => {
    const ref = createReferencePoint();
    const seen = [];
    ref.subscribe((point, source) => seen.push({ point, source }));
    ref.set({ lat: 1, lng: 2 }, 'current');
    expect(seen).toHaveLength(1);
    expect(seen[0].point).toEqual({ lat: 1, lng: 2 });
    expect(seen[0].source).toBe('current');
  });

  it('不正な座標は無視して直前の値を保つ', () => {
    const ref = createReferencePoint();
    const before = ref.get();
    ref.set({ lat: 'abc', lng: 139 }, 'current');
    expect(ref.get()).toEqual(before);
  });

  it('返す座標はコピーで、外から書き換えても内部状態が壊れない', () => {
    const ref = createReferencePoint();
    const point = ref.get();
    point.lat = 999;
    expect(ref.get().lat).not.toBe(999);
  });
});
