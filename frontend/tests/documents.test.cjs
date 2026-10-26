const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function setup() {
  let options;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/app.js'), 'utf8'), {
    location: { pathname: '/documents', search: '' }, URLSearchParams, FormData,
    VoiceChat: { supported: () => false }, Vue: { createApp: value => { options = value; return { mount() {} }; } },
  });
  const app = options.data();
  app.$refs = {};
  for (const [key, fn] of Object.entries(options.methods)) app[key] = fn.bind(app);
  app.loadHealth = async () => {};
  return app;
}
const pdf = name => new File(['%PDF-test'], name, { type: 'application/pdf' });

test('business uploads run sequentially, preserve success, and retry only failures', async () => {
  const app = setup(); app.source = 'web';
  app.dropFiles({ dataTransfer: { files: [pdf('a.pdf'), pdf('b.pdf')] } });
  let calls = 0, active = 0, peak = 0;
  app.api = async (route, { body }) => {
    assert.equal(route, '/api/rag/upload-notice');
    assert.equal(body.has('category'), false);
    calls++; active++; peak = Math.max(peak, active);
    await Promise.resolve(); active--;
    if (calls === 2) throw new Error('실패');
    return { message: '완료' };
  };
  await app.upload();
  assert.equal(peak, 1);
  assert.deepEqual(Array.from(app.selectedFiles, item => item.status), ['완료', '실패']);
  await app.upload();
  assert.equal(calls, 3);
  assert.equal(app.selectedFiles[1].status, '완료');
});

test('law uploads keep subcategory and show duplicate exclusion', async () => {
  const app = setup(); app.category = '우대기업';
  app.addFiles([pdf('law.pdf'), pdf('law.pdf'), new File(['x'], 'bad.txt')]);
  assert.equal(app.selectedFiles.length, 1);
  assert.ok(app.error);
  app.api = async (route, { body }) => {
    assert.equal(route, '/api/rag/upload-paper');
    assert.equal(body.get('category'), '우대기업');
    return { skipped: true, message: '동일 자료' };
  };
  await app.upload();
  assert.equal(app.selectedFiles[0].status, '중복 제외');
  app.selectSource('web');
  assert.equal(app.selectedFiles.length, 0);
});
