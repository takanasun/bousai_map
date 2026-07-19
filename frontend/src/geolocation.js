/**
 * 現在地の取得と「基準地点」の管理。
 *
 * 基準地点は、AIへの質問（「ここから一番近い…」）と距離計算の起点になる。
 * 既定は自宅座標で、現在地を取得したらそちらへ切り替わる。
 *
 * ブラウザ API を直接触るのはこのファイルだけに閉じ込め、
 * `navigator.geolocation` は引数で受け取ってテスト可能にしている。
 */

import { DEFAULT_CENTER } from './mapConfig.js';

/** 利用者に見せるエラーメッセージ（仕様 7.1: 画面を真っ白にしない）。 */
export const GEOLOCATION_MESSAGES = {
  UNSUPPORTED: 'このブラウザは現在地の取得に対応していません',
  PERMISSION_DENIED:
    '現在地の利用が許可されませんでした。ブラウザの設定から許可してください',
  POSITION_UNAVAILABLE: '現在地を取得できませんでした（電波状況をご確認ください）',
  TIMEOUT: '現在地の取得がタイムアウトしました',
  UNKNOWN: '現在地の取得に失敗しました',
};

/** 位置取得のタイムアウト（ミリ秒）。 */
export const GEOLOCATION_TIMEOUT_MS = 10000;

function messageForError(error) {
  switch (error && error.code) {
    case 1:
      return GEOLOCATION_MESSAGES.PERMISSION_DENIED;
    case 2:
      return GEOLOCATION_MESSAGES.POSITION_UNAVAILABLE;
    case 3:
      return GEOLOCATION_MESSAGES.TIMEOUT;
    default:
      return GEOLOCATION_MESSAGES.UNKNOWN;
  }
}

/**
 * 現在地を1回だけ取得する。
 *
 * @param {Geolocation|null} geolocation `navigator.geolocation` 相当
 * @returns {Promise<{lat: number, lng: number, accuracy: number}>}
 */
export function getCurrentPosition(geolocation) {
  return new Promise((resolve, reject) => {
    if (!geolocation || typeof geolocation.getCurrentPosition !== 'function') {
      reject(new Error(GEOLOCATION_MESSAGES.UNSUPPORTED));
      return;
    }

    geolocation.getCurrentPosition(
      (position) => {
        const coords = position.coords;
        resolve({
          lat: coords.latitude,
          lng: coords.longitude,
          accuracy: coords.accuracy,
        });
      },
      (error) => reject(new Error(messageForError(error))),
      {
        enableHighAccuracy: true,
        timeout: GEOLOCATION_TIMEOUT_MS,
        maximumAge: 0,
      },
    );
  });
}

function isValidPoint(point) {
  return (
    point &&
    Number.isFinite(Number(point.lat)) &&
    Number.isFinite(Number(point.lng))
  );
}

/**
 * 距離計算とAIへの質問に使う「基準地点」を保持する。
 *
 * 座標を各所で直接参照すると、現在地や住所に切り替えたときに
 * 更新漏れが起きる。この1か所に集約し、購読で各所へ伝える。
 *
 * @param {{lat: number, lng: number}} [initial] 既定は神奈川県庁
 */
export function createReferencePoint(initial = DEFAULT_CENTER) {
  let point = { lat: initial.lat, lng: initial.lng };
  let source = 'default'; // 'default' | 'current' | 'address'
  const listeners = [];

  function notify() {
    for (const listener of listeners) listener({ ...point }, source);
  }

  return {
    /** 現在の基準地点（コピーを返すので外から壊せない）。 */
    get() {
      return { ...point };
    },
    /** 由来。'default' | 'current' | 'address' */
    source() {
      return source;
    },
    /** 既定地点（神奈川県庁）のままか。 */
    isDefault() {
      return source === 'default';
    },
    /**
     * 基準地点を更新する。不正な座標は無視して直前の値を保つ。
     * @param {{lat:number,lng:number}} next
     * @param {'default'|'current'|'address'} nextSource
     */
    set(next, nextSource = 'current') {
      if (!isValidPoint(next)) return false;
      point = { lat: Number(next.lat), lng: Number(next.lng) };
      source = nextSource;
      notify();
      return true;
    },
    /** 既定地点に戻す。 */
    reset() {
      point = { lat: initial.lat, lng: initial.lng };
      source = 'default';
      notify();
    },
    /** 変更を購読する。 */
    subscribe(listener) {
      listeners.push(listener);
      return () => {
        const index = listeners.indexOf(listener);
        if (index >= 0) listeners.splice(index, 1);
      };
    },
  };
}
