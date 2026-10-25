/* Browser audio lifecycle, separate from the existing RAG request. */
(() => {
  const MAX_SECONDS = 60;
  const MAX_BYTES = 12 * 1024 * 1024;
  const supported = () => !!(window.isSecureContext && navigator.mediaDevices?.getUserMedia && window.MediaRecorder);

  function create({ onState, onAudio, onText, onSpeech }) {
    let recorder, stream, timer, ticker, recordingRequest, speechRequest;
    let captureId = 0, speechId = 0, audioUrl = '';
    const state = { recordingState: 'idle', recordingSeconds: 0, voiceMessage: '', voiceError: '', speechLoading: false, speechError: '', audioUrl: '' };
    const update = patch => { Object.assign(state, patch); onState({ ...state }); };
    const releaseTracks = () => {
      clearTimeout(timer); clearInterval(ticker);
      stream?.getTracks().forEach(track => { track.onended = null; track.stop(); });
      stream = null;
    };
    function cancelRecording() {
      captureId++;
      recordingRequest?.abort();
      if (recorder) {
        recorder.onstop = recorder.ondataavailable = recorder.onerror = null;
        if (recorder.state !== 'inactive') recorder.stop();
      }
      recorder = null;
      releaseTracks();
      update({ recordingState: 'idle', recordingSeconds: 0, voiceMessage: '', voiceError: '' });
    }
    function stopRecording() {
      if (state.recordingState !== 'recording') return;
      update({ recordingState: 'transcribing', voiceMessage: '음성을 글로 바꾸고 있어요. 첫 사용에는 시간이 더 걸릴 수 있어요.' });
      clearTimeout(timer); clearInterval(ticker);
      recorder.stop();
      releaseTracks();
    }
    async function startRecording() {
      if (state.recordingState !== 'idle') return;
      if (!supported()) { update({ voiceError: '음성 입력은 HTTPS 또는 localhost에서 지원되는 브라우저로 이용해 주세요.' }); return; }
      resetSpeech();
      const id = ++captureId;
      update({ recordingState: 'requesting', recordingSeconds: 0, voiceError: '', voiceMessage: '마이크 사용 권한을 확인하고 있어요.' });
      try {
        const acquired = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (id !== captureId) { acquired.getTracks().forEach(track => track.stop()); return; }
        stream = acquired;
        const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
        recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
        const chunks = [];
        let bytes = 0;
        const type = recorder.mimeType || mimeType || 'audio/webm';
        recorder.ondataavailable = event => {
          if (id !== captureId || !event.data.size) return;
          bytes += event.data.size;
          if (bytes > MAX_BYTES) {
            cancelRecording();
            update({ voiceError: '녹음 용량이 커요. 질문을 짧게 다시 녹음해 주세요.' });
            return;
          }
          chunks.push(event.data);
        };
        recorder.onerror = () => {
          if (id !== captureId) return;
          cancelRecording();
          update({ voiceError: '녹음을 계속할 수 없어요. 마이크를 확인해 주세요.' });
        };
        recorder.onstop = async () => {
          if (id !== captureId) return;
          releaseTracks();
          recorder = null;
          update({ recordingState: 'transcribing', voiceMessage: '음성을 글로 바꾸고 있어요. 첫 사용에는 시간이 더 걸릴 수 있어요.' });
          try {
            const audio = new Blob(chunks, { type });
            if (!audio.size) throw new Error('녹음된 음성이 없어요. 다시 녹음해 주세요.');
            recordingRequest = new AbortController();
            const result = await onAudio(audio, recordingRequest.signal);
            if (id !== captureId) return;
            const text = result.text?.trim();
            if (!text) throw new Error('음성을 인식하지 못했어요. 다시 녹음해 주세요.');
            onText(text);
            update({ voiceMessage: '인식한 질문을 확인·수정한 후 답변 받기를 눌러 주세요.' });
          } catch (error) {
            if (id === captureId && error.name !== 'AbortError') update({ voiceError: error.message, voiceMessage: '' });
          } finally {
            if (id === captureId) update({ recordingState: 'idle' });
          }
        };
        stream.getTracks().forEach(track => { track.onended = stopRecording; });
        recorder.start(1000);
        update({ recordingState: 'recording', voiceMessage: '듣고 있어요. 녹음 완료를 누르면 질문으로 바꿔 드려요.' });
        ticker = setInterval(() => update({ recordingSeconds: Math.min(MAX_SECONDS, state.recordingSeconds + 1) }), 1000);
        timer = setTimeout(stopRecording, MAX_SECONDS * 1000);
      } catch (error) {
        if (id !== captureId) return;
        cancelRecording();
        const message = error.name === 'NotAllowedError' ? '마이크 사용을 허용해 주세요. 텍스트로도 질문할 수 있어요.' : error.name === 'NotFoundError' ? '마이크를 찾을 수 없어요. 연결 상태를 확인해 주세요.' : '녹음을 시작하지 못했어요. 마이크와 브라우저 설정을 확인해 주세요.';
        update({ voiceError: message });
      }
    }
    function resetSpeech() {
      speechId++;
      speechRequest?.abort();
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      audioUrl = '';
      update({ speechLoading: false, speechError: '', audioUrl: '' });
    }
    async function loadSpeech(text) {
      if (state.speechLoading || audioUrl) return;
      const id = ++speechId;
      speechRequest = new AbortController();
      update({ speechLoading: true, speechError: '' });
      try {
        const blob = await onSpeech(text, speechRequest.signal);
        if (id !== speechId) return;
        if (!blob.size) throw new Error('음성을 재생할 수 없어요. 다시 시도해 주세요.');
        audioUrl = URL.createObjectURL(blob);
        update({ audioUrl });
      } catch (error) {
        if (id === speechId && error.name !== 'AbortError') update({ speechError: error.message });
      } finally {
        if (id === speechId) update({ speechLoading: false });
      }
    }
    const hide = () => { if (document.hidden) cancelRecording(); };
    const leave = () => { cancelRecording(); resetSpeech(); };
    document.addEventListener('visibilitychange', hide);
    window.addEventListener('pagehide', leave);
    return {
      startRecording, stopRecording, cancelRecording, loadSpeech, resetSpeech,
      dispose() { leave(); document.removeEventListener('visibilitychange', hide); window.removeEventListener('pagehide', leave); },
    };
  }
  window.VoiceChat = { supported, create };
})();
