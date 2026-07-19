import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // 純粋ロジック（色計算・GeoJSON変換・フィルタ・API通信）のみを対象とし、
    // Azure Maps Web SDK に依存する描画層は main.js に隔離している。
    environment: 'node',
    include: ['frontend/tests/**/*.test.js'],
  },
});
