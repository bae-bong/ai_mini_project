/* Four server routes share this Vue app; API tokens stay in HttpOnly cookies. */
(() => {
  const page = { '/login': 'login', '/signup': 'signup', '/chat': 'chat', '/documents': 'documents' }[location.pathname] || 'chat';
  const { createApp } = Vue;
  createApp({
    data: () => ({
      page, user: null, checking: true, busy: false, error: '', sessionError: '', health: null,
      healthError: '', loadingHealth: false, source: 'pdf', question: '',
      submittedQuestion: '', answer: null, selectedFile: null, category: '노무',
      uploadResult: null, managed: false,
      account: { username: '', password: '', confirm: '', employees: 0, types: [] },
      sections: [{ value: 'pdf', label: '경영지원' }, { value: 'web', label: '사업공고' }],
      companyTypes: ['소상공인', '소기업·중소기업', '창업기업', '청년창업기업', '여성기업', '장애인기업', '벤처기업', '사회적기업'],
      authNotice: new URLSearchParams(location.search).has('registered') ? '가입이 완료됐어요. 등록한 아이디로 로그인해 주세요.' : new URLSearchParams(location.search).has('expired') ? '로그인이 만료됐어요. 다시 로그인해 주세요.' : '',
    }),
    computed: {
      isAuthPage() { return ['login', 'signup'].includes(this.page); },
      sourceLabel() { return this.source === 'pdf' ? '경영지원' : '사업공고'; },
      sourceReady() { return !!this.health?.[this.source === 'pdf' ? 'pdf_index' : 'web_index']; },
      canAsk() { return !this.busy && this.sourceReady && !!this.question.trim(); },
      employeeLabel() { const value = this.user?.persona?.employees; return value == null ? '미등록' : `${value}명`; },
      answerSources() { return Array.isArray(this.answer?.sources) ? this.answer.sources : []; },
      examples() { return this.source === 'pdf' ? ['상시근로자 5명 미만인 회사에는 어떤 근로 규정이 적용되나요?', '입사 1년 미만 직원의 연차휴가는 어떻게 확인하나요?', '퇴직금 지급 대상과 기준을 알려주세요.'] : ['청년을 채용할 때 확인할 수 있는 지원사업이 있나요?', '현재 신청할 수 있는 고용 관련 사업공고를 찾아주세요.', '창업기업을 위한 지원사업의 신청 조건이 궁금해요.']; },
    },
    methods: {
      async api(path, options = {}) {
        let response;
        try { response = await fetch(path, { credentials: 'include', ...options }); }
        catch { throw new Error('서버에 연결할 수 없어요. 연결 상태를 확인하고 다시 시도해 주세요.'); }
        const text = await response.text();
        let data;
        try { data = JSON.parse(text); } catch { data = { detail: text }; }
        if (!response.ok) {
          if (response.status === 401 && !this.isAuthPage) location.replace('/login?expired=1');
          const message = this.errorMessage(response.status, data);
          throw Object.assign(new Error(message), { status: response.status });
        }
        return data;
      },
      errorMessage(status, data) {
        const detail = typeof data.detail === 'string' ? data.detail : data.error || '';
        if (status === 401) return '아이디 또는 비밀번호를 확인해 주세요.';
        if (/username already exists/.test(detail)) return '이미 사용 중인 아이디예요. 다른 아이디를 입력해 주세요.';
        if (status === 413) return '파일 용량이 너무 커요. 20MB 이하의 파일을 선택해 주세요.';
        if (status === 409) return '운영 담당자가 관리하는 자료예요. 담당자에게 자료 변경을 요청해 주세요.';
        if (status >= 500) return '요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.';
        if (/employees/.test(detail)) return '상시근로자 수를 0 이상의 정수로 입력해 주세요.';
        if (status === 422) return '파일에서 텍스트를 읽을 수 없어요. 텍스트가 포함된 PDF를 선택해 주세요.';
        if (/로딩되지|벡터화/.test(detail)) return '상담 자료가 아직 준비되지 않았어요. 자료 상태를 새로고침해 주세요.';
        return '입력 내용을 확인하고 다시 시도해 주세요.';
      },
      async initialize() {
        this.checking = true;
        this.error = '';
        try {
          // RAG's JWT check is authoritative for the protected workspace.
          const response = await fetch('/api/rag/me', { credentials: 'include' });
          if (response.status === 401) {
            if (!this.isAuthPage) location.replace('/login');
            return;
          }
          if (!response.ok) throw new Error('로그인 상태를 확인할 수 없어요. 잠시 후 다시 시도해 주세요.');
          this.user = await response.json();
          this.user.persona ||= { employees: null, company_types: [] };
          this.user.persona.company_types ||= [];
          if (this.isAuthPage) { location.replace('/chat'); return; }
          await this.loadHealth();
        } catch (error) { if (!this.isAuthPage) this.error = error.message; }
        finally { this.checking = false; }
      },
      async loadHealth() {
        this.loadingHealth = true;
        this.healthError = '';
        try { this.health = await this.api('/api/rag/health'); }
        catch { this.health = null; this.healthError = '자료 준비 상태를 확인할 수 없어요. 잠시 후 다시 확인해 주세요.'; }
        finally { this.loadingHealth = false; }
      },
      async submitAuth() {
        if (this.busy) return;
        this.error = '';
        const signup = this.page === 'signup';
        if (signup && this.account.password !== this.account.confirm) { this.error = '비밀번호가 서로 달라요. 다시 확인해 주세요.'; return; }
        const employees = Number(this.account.employees);
        if (signup && (!Number.isInteger(employees) || employees < 0 || employees > 2147483647 || this.account.employees === '')) { this.error = '상시근로자 수를 0 이상의 정수로 입력해 주세요.'; return; }
        this.busy = true;
        try {
          const body = new URLSearchParams({ username: this.account.username.trim(), password: this.account.password });
          if (signup) { body.set('employees', employees); body.set('companyTypes', this.account.types.join(',')); }
          await this.api(`/api/auth/${signup ? 'signup' : 'login'}`, { method: 'POST', body });
          // Do not keep accessToken in localStorage or follow backend localhost redirects.
          location.assign(signup ? '/login?registered=1' : '/chat');
        } catch (error) { this.error = error.message; }
        finally { this.busy = false; }
      },
      async logout() {
        this.busy = true;
        this.sessionError = '';
        try { await this.api('/api/auth/logout', { method: 'POST' }); location.assign('/login'); }
        catch (error) { this.sessionError = error.message; }
        finally { this.busy = false; }
      },
      selectSource(value) {
        if (this.busy || this.source === value) return;
        this.source = value;
        this.error = '';
        this.answer = null;
        this.question = '';
      },
      onTabKey(event, current) {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 'pdf' : event.key === 'End' ? 'web' : current === 'pdf' ? 'web' : 'pdf';
        this.selectSource(next);
        this.$nextTick(() => document.getElementById(`${next}-tab`).focus());
      },
      focusQuestion() { document.getElementById('question').focus(); },
      async ask() {
        if (!this.canAsk) return;
        this.busy = true;
        this.error = '';
        this.answer = null;
        this.submittedQuestion = this.question.trim();
        try {
          this.answer = await this.api('/api/rag/rag-query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt: this.submittedQuestion, source: this.source, top_k: 5 }) });
        } catch (error) { this.error = error.message; }
        finally { this.busy = false; }
      },
      safeUrl(value) {
        if (!value) return null;
        try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; }
      },
      personaText(persona) {
        return [persona.employees == null ? '' : `상시근로자 ${persona.employees}명`, ...(persona.company_types || [])].filter(Boolean).join(' · ') || '기업 정보 미등록';
      },
      reviewText(reason) { return reason || '적용 조건·예외를 원문에서 확인하고, 개별 사안의 법적 판단은 노무사 등 전문가에게 확인해 주세요.'; },
      fileSize(size) { return `${(size / 1024 / 1024).toFixed(1)}MB`; },
      chooseFile(event) {
        this.error = '';
        this.uploadResult = null;
        this.selectedFile = null;
        const file = event.target.files[0];
        if (!file) return;
        if (!file.name.toLowerCase().endsWith('.pdf')) this.error = 'PDF 파일을 선택해 주세요.';
        else if (!file.size || file.size > 20 * 1024 * 1024) this.error = '비어 있지 않은 20MB 이하의 파일을 선택해 주세요.';
        else this.selectedFile = file;
        if (!this.selectedFile) event.target.value = '';
      },
      async upload() {
        if (!this.selectedFile || this.busy || this.managed) return;
        this.busy = true;
        this.error = '';
        this.uploadResult = null;
        try {
          const body = new FormData(); body.append('file', this.selectedFile); body.append('category', this.category);
          this.uploadResult = await this.api('/api/rag/upload-paper', { method: 'POST', body });
          this.selectedFile = null;
          document.getElementById('document-file').value = '';
          await this.loadHealth();
        } catch (error) { if (error.status === 409) this.managed = true; else this.error = error.message; }
        finally { this.busy = false; }
      },
    },
    mounted() { document.title = `${{ login: '로그인', signup: '회원가입', chat: 'AI 상담', documents: '자료 관리' }[this.page]} · 경영지원 AI`; this.initialize(); },
  }).mount('#app');
})();
