const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../assets/voice.js'), 'utf8');

function setup(overrides = {}) {
  let state, text = '', uploads = 0, stopped = 0, recorder;
  const revoked = [], timers = new Map();
  const track = { stop() { stopped++; } };
  const stream = { getTracks: () => [track] };
  class Recorder {
    static isTypeSupported(type) { return type === (overrides.mimeType || 'audio/webm'); }
    constructor(_stream, options) { recorder = this; this.mimeType = options?.mimeType || 'audio/webm'; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['recorded']) }); this.onstop?.(); });
    }
  }
  const listeners = {};
  const context = {
    window: { isSecureContext: true, MediaRecorder: Recorder, addEventListener: (key, fn) => { listeners[key] = fn; }, removeEventListener: key => { delete listeners[key]; } },
    document: { hidden: false, addEventListener: (key, fn) => { listeners[key] = fn; }, removeEventListener: key => { delete listeners[key]; } },
    navigator: { mediaDevices: { getUserMedia: overrides.getUserMedia || (async () => stream) } },
    MediaRecorder: Recorder, Blob, AbortController,
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL: value => revoked.push(value) },
    setTimeout: (fn, delay) => { timers.set(delay, fn); return delay; }, clearTimeout: key => timers.delete(key),
    setInterval: (fn, delay) => { timers.set(delay, fn); return delay; }, clearInterval: key => timers.delete(key),
  };
  vm.runInNewContext(source, context);
  const controller = context.window.VoiceChat.create({
    onState: value => { state = value; },
    onAudio: overrides.onAudio || (async () => { uploads++; return { text: '청년 채용 지원사업을 찾아주세요' }; }),
    onText: value => { text = value; },
    onSpeech: overrides.onSpeech || (async () => new Blob(['ID3-audio'], { type: 'audio/mpeg' })),
  });
  return { controller, context, timers, revoked, stream, track, listeners,
    get state() { return state; }, get text() { return text; }, get uploads() { return uploads; },
    get stopped() { return stopped; }, get recorder() { return recorder; } };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('recording fills the editable question and releases the microphone', async () => {
  const app = setup();
  await app.controller.startRecording();
  assert.equal(app.state.recordingState, 'recording');
  app.controller.stopRecording();
  await flush();
  assert.equal(app.text, '청년 채용 지원사업을 찾아주세요');
  assert.equal(app.uploads, 1);
  assert.equal(app.state.recordingState, 'idle');
  assert.ok(app.stopped);
  assert.equal(app.timers.size, 0);
  app.controller.dispose();
});

test('MP4 recorder fallback supports browsers without WebM', async () => {
  const app = setup({ mimeType: 'audio/mp4' });
  await app.controller.startRecording();
  assert.equal(app.recorder.mimeType, 'audio/mp4');
  app.controller.cancelRecording();
  await flush();
  assert.equal(app.uploads, 0);
});

test('recording automatically stops at 60 seconds', async () => {
  const app = setup();
  await app.controller.startRecording();
  app.timers.get(60000)();
  await flush();
  assert.equal(app.uploads, 1);
  assert.equal(app.state.recordingState, 'idle');
  app.controller.dispose();
});

test('permission denial leaves text input available', async () => {
  const app = setup({ getUserMedia: async () => { throw Object.assign(new Error(), { name: 'NotAllowedError' }); } });
  await app.controller.startRecording();
  assert.equal(app.state.recordingState, 'idle');
  assert.match(app.state.voiceError, /허용/);
  app.controller.dispose();
});

test('cancelling while permission is pending releases a late stream', async () => {
  let grant;
  const app = setup({ getUserMedia: () => new Promise(resolve => { grant = resolve; }) });
  const start = app.controller.startRecording();
  app.controller.cancelRecording();
  grant(app.stream);
  await start;
  assert.equal(app.stopped, 1);
  assert.equal(app.state.recordingState, 'idle');
  assert.equal(app.uploads, 0);
  app.controller.dispose();
});

test('late STT response cannot overwrite a question after cancellation', async () => {
  let finish, signal;
  const app = setup({ onAudio: (_blob, abortSignal) => { signal = abortSignal; return new Promise(resolve => { finish = resolve; }); } });
  await app.controller.startRecording();
  app.controller.stopRecording();
  await flush();
  app.controller.cancelRecording();
  assert.ok(signal.aborted);
  finish({ text: 'obsolete question' });
  await flush();
  assert.equal(app.text, '');
  app.controller.dispose();
});

test('failed STT releases capture and reports a recoverable error', async () => {
  const app = setup({ onAudio: async () => { throw new Error('음성 인식 실패'); } });
  await app.controller.startRecording();
  app.controller.stopRecording();
  await flush();
  assert.equal(app.state.recordingState, 'idle');
  assert.match(app.state.voiceError, /인식 실패/);
  assert.ok(app.stopped);
  app.controller.dispose();
});

test('answer audio is cached and revoked on reset', async () => {
  let calls = 0;
  const app = setup({ onSpeech: async () => { calls++; return new Blob(['audio']); } });
  await app.controller.loadSpeech('answer');
  await app.controller.loadSpeech('answer');
  assert.equal(calls, 1);
  assert.equal(app.state.audioUrl, 'blob:test');
  app.controller.resetSpeech();
  assert.deepEqual(app.revoked, ['blob:test']);
  assert.equal(app.state.audioUrl, '');
  app.controller.dispose();
});

test('late speech response cannot attach to a new answer', async () => {
  let finish;
  const app = setup({ onSpeech: () => new Promise(resolve => { finish = resolve; }) });
  const request = app.controller.loadSpeech('old answer');
  app.controller.resetSpeech();
  finish(new Blob(['audio']));
  await request;
  assert.equal(app.state.audioUrl, '');
  app.controller.dispose();
});

test('leaving the page cancels capture and releases answer audio', async () => {
  const app = setup();
  await app.controller.loadSpeech('answer');
  await app.controller.startRecording();
  app.listeners.pagehide();
  await flush();
  assert.equal(app.state.recordingState, 'idle');
  assert.equal(app.state.audioUrl, '');
  assert.equal(app.uploads, 0);
  assert.ok(app.stopped);
  app.controller.dispose();
});
