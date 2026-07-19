/**
 * 地図描画のエントリポイント（Azure Maps Web SDK との結線層）。
 *
 * SDK に依存するのはこのファイルだけに閉じ込め、色計算・GeoJSON変換・
 * フィルタ・現在地取得といった純粋ロジックは別モジュールに分離している。
 *
 * 描画方針（仕様 7.1 のパフォーマンス要件）:
 *   * メッシュ・トイレ1,953件・避難所4,265件・医療機関310件を
 *     HTMLマーカー（DOM要素）で描かず、DataSource + レイヤーで GPU 側に描かせる。
 *   * 表示の切り替えは DataSource を作り直さず、レイヤーの filter だけを差し替える。
 */

import {
  fetchMapConfig,
  fetchMesh,
  fetchToilets,
  fetchEvacuationSites,
  fetchHospitals,
  fetchLandPrice,
  postChat,
  geocodeAddress,
  ApiError,
} from './api.js';
import {
  meshToFeatureCollection,
  toiletsToFeatureCollection,
  sheltersToFeatureCollection,
  hospitalsToFeatureCollection,
} from './geojson.js';
import {
  buildFillColorExpression,
  PALETTE_NORMAL,
  PALETTE_CUD,
  buildFillOpacityExpression,
  getDensityStops,
  densityToColor,
} from './colorScale.js';
import { buildToiletFilterExpression, isMultifunctionToilet } from './filters.js';
import {
  DISASTER_KEYS,
  DISASTER_LABELS,
  buildShelterFilterExpression,
  buildShelterIconExpression,
  filterShelters,
} from './shelters.js';
import {
  HOSPITAL_ICON,
  hospitalIconDataUri,
  collectSpecialities,
  formatSpecialityLines,
  filterHospitals,
  buildHospitalFilterExpression,
} from './hospitals.js';
import { getCurrentPosition, createReferencePoint } from './geolocation.js';
import {
  formatPrice,
  resolvePriceRange,
  filterLandsByMaxPrice,
  filterAreasByMaxPrice,
  landsToFeatureCollection,
  buildPriceBreaks,
  buildLandPriceIconExpression,
  priceIconIds,
  squareIconDataUri,
  PRICE_COLORS,
} from './landprice.js';
import {
  HIGHLIGHT_ICONS,
  HIGHLIGHT_COLORS,
  matchMentionedFacilities,
} from './highlight.js';
import {
  DEFAULT_CENTER,
  DEFAULT_CENTER_NAME,
  INITIAL_ZOOM,
  VIEW_RADIUS_KM,
} from './mapConfig.js';
import { ABOUT_TITLE, ABOUT_PARAGRAPHS, nextAboutState } from './about.js';

/** 距離計算とAIへの質問の起点。現在地取得で自宅座標から切り替わる。 */
const referencePoint = createReferencePoint();

/** 画面上部のステータス表示。 */
function setStatus(message, kind = 'info') {
  const el = document.getElementById('status');
  if (!el) return;
  el.textContent = message;
  el.dataset.kind = kind;
  el.hidden = !message;
}

function setText(elementId, text) {
  const el = document.getElementById(elementId);
  if (el) el.textContent = text;
}

/**
 * 解像度が変わったことを配色の切り替え処理へ伝えるフック。
 * 配色を切り替えたとき、どの解像度で塗り直すか分からないと凡例とずれるため。
 */
let onResolutionApplied = () => {};

/** 配色の保存キー。localStorage が使えない環境でも動くよう例外は握り潰す。 */
const PALETTE_STORAGE_KEY = 'bousai-map:density-palette';

/** 現在の配色。チェックボックスと localStorage で切り替える。 */
let densityPalette = PALETTE_NORMAL;

/**
 * 保存された配色を読む。
 *
 * プライベートブラウジング等で localStorage が例外を投げることがあるため、
 * 失敗しても既定配色で動き続ける（配色は本質的な機能ではない）。
 */
function loadDensityPalette() {
  try {
    return window.localStorage.getItem(PALETTE_STORAGE_KEY) === PALETTE_CUD
      ? PALETTE_CUD
      : PALETTE_NORMAL;
  } catch {
    return PALETTE_NORMAL;
  }
}

function saveDensityPalette(palette) {
  try {
    window.localStorage.setItem(PALETTE_STORAGE_KEY, palette);
  } catch {
    // 保存できなくても今回のセッションでは切り替わっているので、何もしない
  }
}

/**
 * 「色覚に配慮した配色」チェックボックスを配線する。
 *
 * @param {Function} repaint 配色が変わったときに地図を塗り直す処理
 */
function setupColorUniversal(repaint) {
  const checkbox = document.getElementById('color-universal');
  if (!checkbox) return;

  densityPalette = loadDensityPalette();
  checkbox.checked = densityPalette === PALETTE_CUD;
  repaint();

  checkbox.addEventListener('change', () => {
    densityPalette = checkbox.checked ? PALETTE_CUD : PALETTE_NORMAL;
    saveDensityPalette(densityPalette);
    repaint();
  });
}

/**
 * 凡例（人口の色対応表）を組み立てる。
 *
 * 区切りは解像度ごとに変わるため、解像度を切り替えたら必ず描き直す。
 * 凡例と地図の色がずれると誤読につながる。
 */
function renderLegend(resolution) {
  const list = document.getElementById('legend-items');
  if (!list) return;
  list.innerHTML = '';
  for (const stop of getDensityStops(resolution, densityPalette)) {
    const li = document.createElement('li');
    const swatch = document.createElement('span');
    swatch.className = 'legend-swatch';
    swatch.style.backgroundColor = densityToColor(stop.density, resolution, densityPalette);
    const label = document.createElement('span');
    label.textContent = `${stop.density.toLocaleString()} 人`;
    li.append(swatch, label);
    list.appendChild(li);
  }

  const unit = document.getElementById('legend-unit');
  if (unit) unit.textContent = resolution ? `1マス（${resolution}）あたりの人口` : '';
}

/**
 * メッシュレイヤーの色スケールを解像度に合わせて更新し、凡例も揃える。
 *
 * セルが細かいほど1マスあたりの人口は下がる。区切りを固定したままだと
 * 250m 以下ではほぼ全マスが最下段=緑になり、濃淡が読み取れなくなる。
 */
function applyMeshColorScale(layer, resolution) {
  if (layer) {
    layer.setOptions({
      fillColor: buildFillColorExpression(resolution, densityPalette),
      fillOpacity: buildFillOpacityExpression(resolution),
    });
  }
  renderLegend(resolution);
}

/** 地価の凡例を、実データの区切りに合わせて描く。 */
function renderLandPriceLegend(prices) {
  const list = document.getElementById('landprice-legend');
  if (!list) return;
  list.innerHTML = '';
  buildPriceBreaks(prices).forEach((price, index) => {
    const color = PRICE_COLORS[index];
    const li = document.createElement('li');
    const swatch = document.createElement('span');
    swatch.className = 'legend-swatch';
    swatch.style.backgroundColor = `rgb(${color.r}, ${color.g}, ${color.b})`;
    const label = document.createElement('span');
    label.textContent = formatPrice(price);
    li.append(swatch, label);
    list.appendChild(li);
  });
}

/** 災害種別の選択肢を組み立てる。 */
function renderDisasterOptions() {
  const select = document.getElementById('disaster-type');
  if (!select) return;
  for (const key of DISASTER_KEYS) {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = DISASTER_LABELS[key];
    select.appendChild(option);
  }
}

/**
 * 診療科の選択肢を、読み込んだ医療機関データから組み立てる。
 * 選択肢をハードコードしないため、データを差し替えても破綻しない。
 */
function renderSpecialityOptions(hospitals) {
  const select = document.getElementById('hospital-speciality');
  if (!select) return;
  for (const name of collectSpecialities(hospitals)) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }
  select.disabled = false;
}

/**
 * 人口メッシュの解像度の選択肢を組み立てる。
 *
 * 解像度は元データ（メッシュコードの桁数）で決まるため、選べる範囲は
 * サーバに置かれたデータ次第。存在しない解像度を出すと選んだ瞬間に
 * エラーになるので、必ずサーバの応答から組み立てる。
 */
function renderResolutionOptions(available, current) {
  const select = document.getElementById('mesh-resolution');
  if (!select) return;

  select.innerHTML = '';
  for (const resolution of available) {
    const option = document.createElement('option');
    option.value = resolution;
    option.textContent = resolution;
    if (resolution === current) option.selected = true;
    select.appendChild(option);
  }
  select.disabled = available.length <= 1;

  const note = document.getElementById('resolution-note');
  if (note) {
    note.textContent = available.length <= 1 ? '※ 該当データを取り込むと選べます' : '';
  }
}

/** AIチャットのログに1件追加する。 */
function appendChatMessage(role, text, meta) {
  const log = document.getElementById('chat-log');
  if (!log) return;

  const item = document.createElement('div');
  item.className = `chat-message chat-${role}`;

  const body = document.createElement('div');
  body.className = 'chat-body';
  body.textContent = text;
  item.appendChild(body);

  if (meta) {
    const note = document.createElement('div');
    note.className = 'chat-meta';
    note.textContent = meta;
    item.appendChild(note);
  }

  log.appendChild(item);
  log.scrollTop = log.scrollHeight;
  return item;
}

async function main() {
  renderDisasterOptions();
  setStatus('設定を読み込んでいます…');

  // --- Azure Maps のキーを Functions から取得（JS にハードコードしない） ---
  let mapConfig;
  try {
    mapConfig = await fetchMapConfig();
  } catch (error) {
    setStatus(`設定の取得に失敗しました: ${error.message}`, 'error');
    return;
  }

  if (!mapConfig.configured) {
    setStatus(
      mapConfig.message ||
        'Azure Maps のキーが未設定です。.env に AZURE_MAPS_SUBSCRIPTION_KEY を設定してください。',
      'error',
    );
    return;
  }

  if (typeof atlas === 'undefined') {
    setStatus('Azure Maps Web SDK を読み込めませんでした（ネットワークを確認してください）', 'error');
    return;
  }

  // --- 地図の初期化 ---
  setStatus('地図を初期化しています…');
  const map = new atlas.Map('map', {
    center: [DEFAULT_CENTER.lng, DEFAULT_CENTER.lat],
    zoom: INITIAL_ZOOM,
    language: 'ja-JP',
    view: 'Auto',
    style: 'road',
    authOptions: {
      authType: 'subscriptionKey',
      subscriptionKey: mapConfig.azureMapsKey,
    },
  });

  map.events.add('error', (e) => {
    setStatus(`地図の読み込みでエラーが発生しました: ${e && e.error ? e.error.message : ''}`, 'error');
  });

  map.events.add('ready', async () => {
    map.controls.add(
      [new atlas.control.ZoomControl(), new atlas.control.CompassControl()],
      { position: 'top-right' },
    );

    const popup = new atlas.Popup({ pixelOffset: [0, -18] });

    // --- 人口密度メッシュ ---
    const meshSource = new atlas.source.DataSource();
    map.sources.add(meshSource);
    const meshLayer = new atlas.layer.PolygonLayer(meshSource, 'mesh-fill', {
      fillColor: buildFillColorExpression(undefined, densityPalette),
      fillOpacity: buildFillOpacityExpression(),
    });
    map.layers.add(meshLayer);

    // --- 避難所 ---
    const shelterSource = new atlas.source.DataSource();
    map.sources.add(shelterSource);
    const shelterLayer = new atlas.layer.SymbolLayer(shelterSource, 'shelter-pins', {
      iconOptions: {
        image: buildShelterIconExpression(),
        allowOverlap: true,
        ignorePlacement: true,
        size: 0.9,
      },
      minZoom: 11,
    });
    map.layers.add(shelterLayer);

    // --- 医療機関（トイレ・避難所と区別できるよう記号を重ねる） ---
    const hospitalSource = new atlas.source.DataSource();
    map.sources.add(hospitalSource);
    // 十字は画像に描き込んである（textOptions で文字を重ねない）。
    // 重ねる方式は em 単位のオフセット指定しかできず、中心に収まらなかった。
    async function registerHospitalIcon() {
      try {
        await map.imageSprite.add(HOSPITAL_ICON, hospitalIconDataUri());
        return true;
      } catch (error) {
        console.error('医療機関アイコンの登録に失敗しました', error);
        return false;
      }
    }

    const hospitalLayer = new atlas.layer.SymbolLayer(hospitalSource, 'hospital-pins', {
      iconOptions: {
        image: HOSPITAL_ICON,
        allowOverlap: true,
        ignorePlacement: true,
        size: 0.55,
      },
      minZoom: 11,
    });
    map.layers.add(hospitalLayer);

    // --- 地価（施設ピンと紛らわしくないよう「正方形」で描く） ---
    // 色は価格帯ごとの画像に焼き込む。SymbolLayer の image は式で色を
    // 変えられないため、段ごとに画像を用意して match 式で切り替える。
    const landSource = new atlas.source.DataSource();
    map.sources.add(landSource);

    /**
     * 価格帯ごとの正方形アイコンを登録する。
     *
     * 組み込みテンプレート(createFromTemplate)ではなく自前のSVGを使う。
     * テンプレート名の解決に失敗すると地図の初期化ごと落ちるため。
     * 失敗しても地図は動かし続ける（地価だけ描けなくなる）。
     */
    async function registerPriceIcons() {
      try {
        await Promise.all(
          priceIconIds().map((id, index) =>
            map.imageSprite.add(id, squareIconDataUri(PRICE_COLORS[index])),
          ),
        );
        return true;
      } catch (error) {
        console.error('地価アイコンの登録に失敗しました', error);
        return false;
      }
    }

    const landLayer = new atlas.layer.SymbolLayer(landSource, 'landprice-squares', {
      iconOptions: {
        image: buildLandPriceIconExpression(),
        allowOverlap: true,
        ignorePlacement: true,
        size: 0.55,
      },
      visible: false,
    });
    map.layers.add(landLayer);

    map.events.add('click', landLayer, (e) => {
      const shape = e.shapes && e.shapes[0];
      if (!shape) return;
      const p = shape.getProperties();
      popup.setOptions({
        content: `<div class="popup">
            <strong>${formatPrice(p.pricePerSqm)} / ㎡</strong>
            <div class="popup-tag">地価公示（${p.year}年）${p.town ? ' / ' + p.town : ''}</div>
            ${p.address ? `<div class="popup-note">${p.address}</div>` : ''}
            ${p.uses && p.uses.length ? `<div>用途: ${p.uses.join('、')}</div>` : ''}
            <div class="popup-note">地点の価格です。同じ町でも場所により差があります。</div>
          </div>`,
        position: shape.getCoordinates(),
      });
      popup.open(map);
    });

    // --- 公衆トイレ ---
    const toiletSource = new atlas.source.DataSource();
    map.sources.add(toiletSource);
    const toiletLayer = new atlas.layer.SymbolLayer(toiletSource, 'toilet-pins', {
      iconOptions: {
        image: 'pin-round-blue',
        allowOverlap: true,
        ignorePlacement: true,
        size: 0.7,
      },
      minZoom: 12,
    });
    map.layers.add(toiletLayer);

    // --- ポップアップ（仕様 5.4） ---
    map.events.add('click', toiletLayer, (e) => {
      const shape = e.shapes && e.shapes[0];
      if (!shape) return;
      const p = shape.getProperties();
      const features = [
        p.accessible ? '車椅子対応' : null,
        p.ostomate ? 'オストメイト対応' : null,
        p.open24h ? '24時間利用可' : null,
      ].filter(Boolean);
      popup.setOptions({
        content: `<div class="popup">
            <strong>${p.name}</strong>
            <div class="popup-tag">公衆トイレ</div>
            <div>${features.length ? features.join(' / ') : '設備情報なし'}</div>
          </div>`,
        position: shape.getCoordinates(),
      });
      popup.open(map);
    });

    map.events.add('click', hospitalLayer, (e) => {
      const shape = e.shapes && e.shapes[0];
      if (!shape) return;
      const p = shape.getProperties();
      const caps = Array.isArray(p.capabilities) ? p.capabilities : [];
      // 診療科は20を超えることがあるため、上限を設けたうえで3件ごとに折り返す
      const shown = caps.slice(0, 12);
      const specialityHtml = shown.length
        ? formatSpecialityLines(shown)
            .map((line) => `<div class="popup-speciality">${line}</div>`)
            .join('') + (caps.length > shown.length ? '<div class="popup-note">ほか</div>' : '')
        : '<div>記載なし</div>';

      popup.setOptions({
        content: `<div class="popup">
            <strong>${p.name}</strong>
            <div class="popup-tag">医療機関${p.isDisasterBase ? '（災害拠点病院）' : ''}</div>
            ${p.address ? `<div class="popup-note">${p.address}</div>` : ''}
            <div><b>診療科:</b></div>
            ${specialityHtml}
            ${p.website ? `<div><a href="${p.website}" target="_blank" rel="noopener">公式サイト</a></div>` : ''}
          </div>`,
        position: shape.getCoordinates(),
      });
      popup.open(map);
    });

    map.events.add('click', shelterLayer, (e) => {
      const shape = e.shapes && e.shapes[0];
      if (!shape) return;
      const p = shape.getProperties();

      const roles = [];
      if (p.isEmergencySite) roles.push('指定緊急避難場所（命を守る）');
      if (p.isEvacuationCenter) roles.push('指定避難所（生活する）');
      if (p.isWelfareShelter) roles.push('福祉避難所');

      const types = Array.isArray(p.disasterTypes) ? p.disasterTypes : [];
      const disasterHtml = p.isEmergencySite
        ? `<div><b>対応災害:</b> ${
            types.length ? types.map((t) => DISASTER_LABELS[t] || t).join('、') : '記載なし'
          }</div>`
        : '<div class="popup-note">生活する場所のため災害種別の指定はありません</div>';

      popup.setOptions({
        content: `<div class="popup">
            <strong>${p.name}</strong>
            <div class="popup-tag">${roles.join(' / ')}</div>
            ${p.address ? `<div class="popup-note">${p.address}</div>` : ''}
            ${disasterHtml}
            ${p.targetOccupants ? `<div><b>受入対象:</b> ${p.targetOccupants}</div>` : ''}
          </div>`,
        position: shape.getCoordinates(),
      });
      popup.open(map);
    });

    // --- AIの回答で言及された施設の強調マーカー ---
    // 一度に数件しか出ないため HtmlMarker(DOM) で十分。既存レイヤーとは
    // 別の色（黄/緑/紫）の旗を立てて、通常のピンと区別する。
    let highlightMarkers = [];

    function clearHighlights() {
      for (const marker of highlightMarkers) map.markers.remove(marker);
      highlightMarkers = [];
    }

    function showHighlights(facilities) {
      clearHighlights();
      for (const f of facilities) {
        const icon = HIGHLIGHT_ICONS[f.kind] || '🚩';
        const color = HIGHLIGHT_COLORS[f.kind] || '#f9a825';
        const marker = new atlas.HtmlMarker({
          position: [f.location.lng, f.location.lat],
          // ::after で同じ絵文字を重ねて光らせるため data-icon に渡す
          htmlContent:
            `<div class="highlight-marker" style="--flag-color:${color}" ` +
            `data-icon="${icon}" title="${f.name}">${icon}</div>`,
        });
        map.markers.add(marker);
        highlightMarkers.push(marker);
      }
    }

    // --- 自宅 / 現在地マーカー ---
    const defaultMarker = new atlas.HtmlMarker({
      position: [DEFAULT_CENTER.lng, DEFAULT_CENTER.lat],
      htmlContent: `<div class="home-marker" title="${DEFAULT_CENTER_NAME}">🏛</div>`,
    });
    map.markers.add(defaultMarker);

    const currentMarker = new atlas.HtmlMarker({
      position: [DEFAULT_CENTER.lng, DEFAULT_CENTER.lat],
      htmlContent: '<div class="current-marker" title="現在地"></div>',
      visible: false,
    });
    map.markers.add(currentMarker);

    // 基準地点が変わったら、マーカーと表示ラベルを追従させる
    referencePoint.subscribe((point, source) => {
      const isDefault = source === 'default';
      currentMarker.setOptions({
        position: [point.lng, point.lat],
        visible: !isDefault,
      });
      defaultMarker.setOptions({ visible: isDefault });
      const labels = {
        current: `基準: 現在地 (${point.lat.toFixed(5)}, ${point.lng.toFixed(5)})`,
        address: `基準: 指定住所 (${point.lat.toFixed(5)}, ${point.lng.toFixed(5)})`,
        default: `基準: ${DEFAULT_CENTER_NAME}（既定）`,
      };
      setText('reference-label', labels[source] || labels.default);
    });
    setText('reference-label', `基準: ${DEFAULT_CENTER_NAME}（既定）`);

    /**
     * 基準地点を移動し、地図も追従させる共通処理。
     * @param {{lat:number,lng:number}} point
     * @param {'home'|'current'} source
     */
    function moveTo(point, source) {
      referencePoint.set(point, source);
      map.setCamera({
        center: [point.lng, point.lat],
        zoom: Math.max(map.getCamera().zoom, INITIAL_ZOOM),
        type: 'fly',
        duration: 1000,
      });
    }

    /**
     * 起動時に基準地点を尋ねるダイアログ。
     *
     * 既定地点（県庁）のまま気付かず使われると、無関係な場所の避難所を
     * 見てしまうため、最初に必ず住所か現在地を選ばせる。
     * 「スキップ」を選んだ場合のみ既定地点で続行する。
     *
     * @returns {Promise<void>} 選択が済むまで解決しない
     */
    function askForStartingPoint() {
      const dialog = document.getElementById('startup-dialog');
      if (!dialog) return Promise.resolve();

      const form = document.getElementById('startup-address-form');
      const input = document.getElementById('startup-address-input');
      const submit = document.getElementById('startup-address-submit');
      const locateBtn = document.getElementById('startup-locate');
      const skipBtn = document.getElementById('startup-skip');
      const errorBox = document.getElementById('startup-error');

      dialog.hidden = false;
      if (input) input.focus();

      function showError(message) {
        if (!errorBox) return;
        errorBox.textContent = message;
        errorBox.hidden = false;
      }

      function setBusy(busy) {
        for (const el of [input, submit, locateBtn, skipBtn]) {
          if (el) el.disabled = busy;
        }
      }

      return new Promise((resolve) => {
        function finish(message) {
          dialog.hidden = true;
          setStatus(message);
          resolve();
        }

        if (form) {
          form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const address = (input && input.value.trim()) || '';
            if (!address) {
              showError('住所を入力してください。');
              return;
            }
            if (errorBox) errorBox.hidden = true;
            setBusy(true);
            try {
              const point = await geocodeAddress(address);
              moveTo(point, 'address');
              finish(`「${address}」を基準にしました。`);
            } catch (error) {
              const message =
                error instanceof ApiError ? error.message : '住所を検索できませんでした';
              showError(message);
              setBusy(false);
            }
          });
        }

        if (locateBtn) {
          locateBtn.addEventListener('click', async () => {
            if (errorBox) errorBox.hidden = true;
            setBusy(true);
            try {
              const position = await getCurrentPosition(
                typeof navigator !== 'undefined' ? navigator.geolocation : null,
              );
              moveTo({ lat: position.lat, lng: position.lng }, 'current');
              finish(`現在地を基準にしました（誤差 約${Math.round(position.accuracy)}m）。`);
            } catch (error) {
              showError(`${error.message}。住所を入力するか、スキップしてください。`);
              setBusy(false);
            }
          });
        }

        if (skipBtn) {
          skipBtn.addEventListener('click', () => {
            finish(
              `${DEFAULT_CENTER_NAME}を基準にしています。` +
                '住所入力か「現在地を取得」でいつでも変更できます。',
            );
          });
        }
      });
    }

    // 地価スライダーの要素。
    // const は巻き上げられても初期化前は参照できない（Temporal Dead Zone）。
    // setupPriceSlider() をデータ読み込み中に呼ぶため、宣言はそれより前に置く。
    const landCheckbox = document.getElementById('show-landprice');
    const priceSlider = document.getElementById('landprice-slider');
    const priceOutput = document.getElementById('landprice-value');

    // --- データ読み込み ---
    setStatus('データを読み込んでいます…');
    let toilets = [];
    let shelters = [];
    let hospitals = [];
    let lands = [];
    let landAreas = [];
    let priceRange = { min: 0, max: 0 };
    try {
      const [meshResult, toiletRecords, shelterRecords, hospitalRecords, landResult] =
        await Promise.all([
          fetchMesh(),
          fetchToilets(),
          fetchEvacuationSites(),
          fetchHospitals(),
          fetchLandPrice(),
        ]);

      meshSource.add(meshToFeatureCollection(meshResult.items));
      applyMeshColorScale(meshLayer, meshResult.resolution);
      renderResolutionOptions(meshResult.availableResolutions, meshResult.resolution);

      // 配色の切り替え。現在の解像度を覚えておき、切り替え時に塗り直す。
      let currentResolution = meshResult.resolution;
      setupColorUniversal(() => {
        applyMeshColorScale(meshLayer, currentResolution);
      });
      onResolutionApplied = (resolution) => {
        currentResolution = resolution;
      };

      toilets = toiletRecords;
      toiletSource.add(toiletsToFeatureCollection(toilets));

      shelters = shelterRecords;
      shelterSource.add(sheltersToFeatureCollection(shelters));

      hospitals = hospitalRecords;
      // 画像の登録はデータ投入より先に済ませる（未登録だとピンが描かれない）
      await registerHospitalIcon();
      hospitalSource.add(hospitalsToFeatureCollection(hospitals));
      renderSpecialityOptions(hospitals);

      // 地価は補助的な機能。ここで失敗しても地図と防災施設は使えるようにする。
      try {
        lands = landResult.items;
        landAreas = landResult.areas;
        // スライダーの範囲は実データから決める（決め打ちだとデータ差し替えで破綻する）
        priceRange = resolvePriceRange(landResult.priceRange);
        // 色の区切りは実データの分布から決める（線形だと99.8%が同色に潰れる）
        const landPrices = lands.map((l) => l.pricePerSqm);
        const landBreaks = buildPriceBreaks(landPrices);
        await registerPriceIcons();
        // 区切りを渡して priceBand を付与する（アイコンの出し分けに使う）
        landSource.add(landsToFeatureCollection(lands, landBreaks));
        renderLandPriceLegend(landPrices);
        setupPriceSlider();
      } catch (error) {
        console.error('地価データの描画に失敗しました', error);
        setText('landprice-count', '地価データを表示できませんでした');
      }

      const multifunctionCount = toilets.filter(isMultifunctionToilet).length;
      setText('toilet-count', `トイレ ${toilets.length.toLocaleString()} 件`);
      setText('shelter-count', `避難所 ${shelters.length.toLocaleString()} 件`);
      setText('hospital-count', `医療機関 ${hospitals.length.toLocaleString()} 件`);
      setText('landprice-count', `地価 ${lands.length.toLocaleString()} 地点`);
      setStatus(
        `メッシュ ${meshResult.items.length.toLocaleString()} 件（${meshResult.resolution}）/ ` +
          `避難所 ${shelters.length.toLocaleString()} 件 / ` +
          `医療機関 ${hospitals.length.toLocaleString()} 件 / ` +
          `トイレ ${toilets.length.toLocaleString()} 件（多機能 ${multifunctionCount.toLocaleString()} 件）を表示中。` +
          `中心から半径 ${VIEW_RADIUS_KM}km が目安です。`,
      );
    } catch (error) {
      // ApiError 以外は JS の実行時エラー。原因が分かるようコンソールにも出す。
      if (!(error instanceof ApiError)) {
        console.error('データの描画中にエラーが発生しました', error);
      }
      const message =
        error instanceof ApiError
          ? error.message
          : `データの描画に失敗しました: ${error && error.message ? error.message : error}`;
      setStatus(message, 'error');
      return;
    }

    // --- 解像度の切り替え ---
    const resolutionSelect = document.getElementById('mesh-resolution');
    if (resolutionSelect) {
      resolutionSelect.addEventListener('change', async () => {
        const resolution = resolutionSelect.value;
        resolutionSelect.disabled = true;
        setStatus(`人口メッシュ（${resolution}）を読み込んでいます…`);
        try {
          const result = await fetchMesh(resolution);
          meshSource.clear();
          meshSource.add(meshToFeatureCollection(result.items));
          applyMeshColorScale(meshLayer, result.resolution);
          onResolutionApplied(result.resolution);
          setStatus(
            `人口メッシュを ${result.resolution} に切り替えました（${result.items.length.toLocaleString()} 件）。`,
          );
        } catch (error) {
          const message = error instanceof ApiError ? error.message : 'データが取得できませんでした';
          setStatus(message, 'error');
        } finally {
          resolutionSelect.disabled = false;
        }
      });
    }

    // --- 地価スライダーの操作（要素は上部で取得済み） ---

    /** スライダーの現在値（円/㎡）。上限いっぱいなら「制限なし」とみなす。 */
    function currentMaxPrice() {
      if (!priceSlider) return null;
      const value = Number(priceSlider.value);
      return value >= Number(priceSlider.max) ? null : value;
    }

    function applyLandPriceFilter() {
      const visible = Boolean(landCheckbox && landCheckbox.checked);
      const maxPrice = currentMaxPrice();

      // 上限以下の地点だけ残す。レイヤーの filter で絞れば再生成が不要
      landLayer.setOptions({
        visible,
        filter: maxPrice === null ? undefined : ['<=', ['get', 'pricePerSqm'], maxPrice],
      });

      // 上限以下のエリアにある避難所・医療機関だけを旗で強調する
      const shownAreas = filterAreasByMaxPrice(landAreas, maxPrice);
      const shownLands = filterLandsByMaxPrice(lands, maxPrice);

      if (priceOutput) {
        priceOutput.textContent = maxPrice === null ? '制限なし' : formatPrice(maxPrice);
      }
      setText(
        'landprice-count',
        `地価 ${shownLands.length.toLocaleString()} / ${lands.length.toLocaleString()} 地点` +
          `（該当エリア ${shownAreas.length}町）`,
      );
      popup.close();
    }

    function setupPriceSlider() {
      if (!priceSlider) return;
      priceSlider.min = String(priceRange.min);
      priceSlider.max = String(priceRange.max);
      // 目盛りは1万円単位。実データの幅に対して細かすぎないようにする
      priceSlider.step = String(Math.max(10000, Math.round((priceRange.max - priceRange.min) / 100)));
      priceSlider.value = String(priceRange.max);
      priceSlider.disabled = false;
      if (landCheckbox) landCheckbox.disabled = false;
      if (priceOutput) priceOutput.textContent = '制限なし';
    }

    for (const el of [landCheckbox, priceSlider]) {
      if (!el) continue;
      el.addEventListener('input', applyLandPriceFilter);
      el.addEventListener('change', applyLandPriceFilter);
    }

    // --- 操作パネルの結線 ---
    const toiletCheckbox = document.getElementById('multifunction-only');
    const emergencyCheckbox = document.getElementById('show-emergency');
    const centerCheckbox = document.getElementById('show-center');
    const disasterSelect = document.getElementById('disaster-type');
    const hospitalCheckbox = document.getElementById('show-hospitals');
    const specialitySelect = document.getElementById('hospital-speciality');

    function applyToiletFilter() {
      const multifunctionOnly = Boolean(toiletCheckbox && toiletCheckbox.checked);
      const expression = buildToiletFilterExpression(multifunctionOnly);
      toiletLayer.setOptions({ filter: expression === null ? undefined : expression });
      popup.close();
      const shown = multifunctionOnly ? toilets.filter(isMultifunctionToilet).length : toilets.length;
      setText('toilet-count', `トイレ ${shown.toLocaleString()} / ${toilets.length.toLocaleString()} 件`);
    }

    function applyShelterFilter() {
      const options = {
        showEmergency: Boolean(emergencyCheckbox && emergencyCheckbox.checked),
        showCenter: Boolean(centerCheckbox && centerCheckbox.checked),
        disasterType: disasterSelect ? disasterSelect.value : '',
      };
      const expression = buildShelterFilterExpression(options);
      shelterLayer.setOptions({ filter: expression === null ? undefined : expression });
      popup.close();
      const shown = filterShelters(shelters, options).length;
      setText('shelter-count', `避難所 ${shown.toLocaleString()} / ${shelters.length.toLocaleString()} 件`);
    }

    function applyHospitalFilter() {
      const visible = Boolean(hospitalCheckbox && hospitalCheckbox.checked);
      const speciality = specialitySelect ? specialitySelect.value : '';
      // 表示ON/OFF はレイヤーごと切り替え、診療科は filter で絞る
      hospitalLayer.setOptions({
        visible,
        filter: buildHospitalFilterExpression(speciality) || undefined,
      });
      popup.close();
      const shown = visible ? filterHospitals(hospitals, speciality).length : 0;
      setText(
        'hospital-count',
        `医療機関 ${shown.toLocaleString()} / ${hospitals.length.toLocaleString()} 件`,
      );
    }

    for (const [element, handler] of [
      [toiletCheckbox, applyToiletFilter],
      [emergencyCheckbox, applyShelterFilter],
      [centerCheckbox, applyShelterFilter],
      [disasterSelect, applyShelterFilter],
      [hospitalCheckbox, applyHospitalFilter],
      [specialitySelect, applyHospitalFilter],
    ]) {
      if (!element) continue;
      element.disabled = false;
      element.addEventListener('change', handler);
    }

    // --- 現在地の取得（機能3） ---
    const locateButton = document.getElementById('locate-button');
    if (locateButton) {
      locateButton.disabled = false;
      locateButton.addEventListener('click', async () => {
        locateButton.disabled = true;
        setStatus('現在地を取得しています…');
        try {
          const position = await getCurrentPosition(
            typeof navigator !== 'undefined' ? navigator.geolocation : null,
          );
          // 基準地点を更新 → マーカーとラベルは購読側が追従する
          moveTo({ lat: position.lat, lng: position.lng }, 'current');
          setStatus(
            `現在地に移動しました（誤差 約${Math.round(position.accuracy)}m）。` +
              'AIへの質問と距離の基準も現在地に切り替わりました。',
          );
        } catch (error) {
          setStatus(error.message, 'error');
        } finally {
          locateButton.disabled = false;
        }
      });
    }

    const homeButton = document.getElementById('home-button');
    if (homeButton) {
      homeButton.disabled = false;
      homeButton.addEventListener('click', () => {
        moveTo({ lat: DEFAULT_CENTER.lat, lng: DEFAULT_CENTER.lng }, 'default');
        setStatus(`${DEFAULT_CENTER_NAME}を基準に戻しました。`);
      });
    }

    // --- 住所で基準地点を指定 ---
    const addressForm = document.getElementById('address-form');
    const addressInput = document.getElementById('address-input');
    if (addressForm && addressInput) {
      addressInput.disabled = false;
      addressForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const address = addressInput.value.trim();
        if (!address) return;

        addressInput.disabled = true;
        setStatus(`「${address}」を検索しています…`);
        try {
          const point = await geocodeAddress(address);
          moveTo(point, 'address');
          setStatus(`「${address}」を基準にしました。`);
        } catch (error) {
          const message = error instanceof ApiError ? error.message : '住所を検索できませんでした';
          setStatus(message, 'error');
        } finally {
          addressInput.disabled = false;
        }
      });
    }

    // 起動時に基準地点を尋ねる（既定地点のまま気付かず使われるのを防ぐ）
    await askForStartingPoint();

    // --- AI防災アシスタント（機能2） ---
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatSend = document.getElementById('chat-send');

    if (mapConfig.chatConfigured === false) {
      // 設定項目名はバックエンド（OpenAI / Azure OpenAI）で異なる。
      // ここに書くと切り替え時に嘘になるため、サーバの案内文をそのまま出す。
      const hint = mapConfig.chatConfigHint || '.env の設定を確認してください。';
      appendChatMessage(
        'assistant',
        `AIアシスタントは未設定です。${hint} 設定後に func を再起動すると使えるようになります。`,
      );
    } else {
      appendChatMessage(
        'assistant',
        '周辺の避難所・医療機関・トイレについて質問できます。' +
          '例：「一番近い避難所はどこ？」「周辺の精神科は？」',
      );
    }

    if (chatForm && chatInput && chatSend) {
      chatInput.disabled = false;
      chatSend.disabled = false;

      chatForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const question = chatInput.value.trim();
        if (!question) return;

        appendChatMessage('user', question);
        chatInput.value = '';
        chatInput.disabled = true;
        chatSend.disabled = true;
        const pending = appendChatMessage('assistant', '考えています…');

        try {
          // 基準地点（自宅または現在地）を送る。周辺データの収集はサーバ側。
          const origin = referencePoint.get();
          const result = await postChat(question, origin);
          const s = result.sources || {};
          pending.querySelector('.chat-body').textContent = result.answer;

          // 回答文に名前が出た施設に旗を立てる
          const mentioned = matchMentionedFacilities(result.answer, result.candidates);
          showHighlights(mentioned);

          const meta = document.createElement('div');
          meta.className = 'chat-meta';
          meta.textContent =
            `根拠: ${referencePoint.isDefault() ? DEFAULT_CENTER_NAME : '基準地点'}から半径${s.radiusKm ?? '?'}km / ` +
            `避難所${s.shelters ?? 0}件・医療機関${s.hospitals ?? 0}件・トイレ${s.toilets ?? 0}件` +
            (mentioned.length ? ` / 地図に${mentioned.length}件を旗で表示` : '');
          pending.appendChild(meta);
        } catch (error) {
          const message = error instanceof ApiError ? error.message : 'AIの応答を取得できませんでした';
          pending.querySelector('.chat-body').textContent = message;
          pending.classList.add('chat-error');
        } finally {
          chatInput.disabled = false;
          chatSend.disabled = false;
          chatInput.focus();
        }
      });
    }
  });
}

/**
 * 企画意図のモーダルを組み立てて、人アイコンに開閉を紐付ける。
 *
 * 地図やAPIに依存しないため main() の外で先に呼ぶ。
 * データ取得が失敗しても「なぜ作ったか」だけは読める状態にしておく。
 */
function setupAbout() {
  const toggle = document.getElementById('about-toggle');
  const modal = document.getElementById('about-modal');
  const title = document.getElementById('about-title');
  const body = document.getElementById('about-body');
  const close = document.getElementById('about-close');
  if (!toggle || !modal || !title || !body || !close) return;

  // textContent で入れる（innerHTML を使わないのでエスケープ漏れが起きない）
  title.textContent = ABOUT_TITLE;
  for (const paragraph of ABOUT_PARAGRAPHS) {
    const p = document.createElement('p');
    p.textContent = paragraph;
    body.appendChild(p);
  }

  const setOpen = (open) => {
    toggle.setAttribute('aria-expanded', String(open));
    modal.hidden = !open;
    // モーダルを開いたらフォーカスを中へ移し、閉じたらアイコンへ戻す。
    // キーボード操作でも背後の地図を触ってしまわないようにするため。
    if (open) {
      close.focus();
    } else {
      toggle.focus();
    }
  };

  toggle.addEventListener('click', () => {
    setOpen(nextAboutState(toggle.getAttribute('aria-expanded')));
  });

  close.addEventListener('click', () => setOpen(false));

  // 背景（オーバーレイ）のクリックで閉じる。
  // パネル内のクリックが親へ伝播しても閉じないよう、対象を厳密に見る。
  modal.addEventListener('click', (event) => {
    if (event.target === modal) setOpen(false);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) setOpen(false);
  });
}

setupAbout();

main().catch((error) => {
  setStatus(`予期せぬエラーが発生しました: ${error.message}`, 'error');
});
