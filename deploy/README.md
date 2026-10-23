# EC2 배포 및 운영

단일 EC2에서 Nginx, Java 인증 서버, FastAPI RAG, PostgreSQL 17 + pgvector를 Docker Compose로 실행한다. S3와 음성 서비스는 이번 구성에 포함하지 않는다. 인증 서버는 현재 JDK HttpServer이며 Spring Boot로 바꾸지 않았다.

## 배포 안내

- [최초 배포: EC2 시작부터 서비스 실행까지](#최초-배포-ec2-시작부터-서비스-실행까지)
- [기존 서버에 GitHub 최신 코드 반영](#기존-서버에-github-최신-코드-반영)
- [다시 켜기와 중지](#다시-켜기와-중지)
- [자주 발생하는 문제](#자주-발생하는-문제)
- [DuckDNS 공개 HTTPS 연결](#duckdns-공개-https-연결)

기본 구성은 **SSH 터널로 접속하는 팀 테스트 구성**이다. EC2의 8080 포트는 외부에 공개하지 않는다. `skala-ai.duckdns.org`를 통한 공개 접속은 아래 [DuckDNS 공개 HTTPS 연결](#duckdns-공개-https-연결)을 적용한다. 공개 모드 적용 후에는 업데이트·재기동 시에도 두 Compose 파일을 함께 사용한다.

명령어를 입력하는 위치를 먼저 구분한다.

| 위치            | 프롬프트 예시      | 실행할 작업                             |
| --------------- | ------------------ | --------------------------------------- |
| Mac 로컬 터미널 | `➜ ~`              | SSH 접속, SSH 터널, 로컬 접속 확인      |
| EC2 터미널      | `ubuntu@ip-...:~$` | GitHub 코드 받기, 환경변수, Docker 실행 |
| Mac 브라우저    | 주소창             | `http://127.0.0.1:18080` 접속           |

## 최초 배포: EC2 시작부터 서비스 실행까지

### 1. AWS 콘솔에서 EC2 시작

1. AWS 콘솔의 EC2 → 인스턴스에서 해당 서버를 선택한다. 현재 서버는 시드니 `ap-southeast-2`에 있으므로 리전도 확인한다.
2. 중지 상태라면 **인스턴스 상태 → 인스턴스 시작**을 선택한다. 이미 실행 중이면 그대로 사용한다.
3. 실행 중 상태와 상태 검사 통과를 확인한다.
4. 인스턴스의 **퍼블릭 IPv4 DNS** 또는 퍼블릭 IPv4 주소를 확인한다.
5. 보안 그룹의 SSH 22 접근은 접속할 관리자의 공인 IP로 제한한다. 현재 구성에서는 8080·8001·5432를 열지 않는다.

중지 후 다시 시작하면 자동 할당된 퍼블릭 IP와 DNS가 달라질 수 있다. 예전 주소 대신 콘솔의 현재 주소를 사용한다. [AWS 시작·중지 안내](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html)

### 2. Mac에서 SSH 접속과 터널 열기

**Mac 로컬 터미널**에서 실행한다. 아래 `EC2_HOST` 값은 마지막으로 사용한 주소 예시이며, 현재 콘솔 주소가 다르면 바꾼다. 키 경로도 실제 파일 위치와 맞춘다.

```sh
EC2_HOST="ec2-32-236-191-133.ap-southeast-2.compute.amazonaws.com"
chmod 400 ~/.ssh/testkey.pem
ssh -i ~/.ssh/testkey.pem \
  -o ExitOnForwardFailure=yes \
  -L 18080:127.0.0.1:8080 \
  "ubuntu@$EC2_HOST"
```

이 명령은 EC2 로그인과 Mac의 SSH 터널을 함께 연다. 접속 후 프롬프트가 `ubuntu@ip-...`로 바뀌면 이후 서버 명령을 실행한다. **브라우저 테스트가 끝날 때까지 이 터미널을 열어둔다.**

`18080`은 Mac에 열리는 포트이고, `8080`은 EC2 내부의 Nginx 포트다. EC2에서 `127.0.0.1:18080`으로 접속하는 것이 아니다.

### 3. EC2에 Docker·Compose·Git 준비

**EC2 터미널**에서 확인한다.

```sh
sudo docker --version
sudo docker compose version
git --version
```

Docker가 없다면 [Docker 설치 절차](#docker-enginecompose-최초-설치)를 먼저 수행한다. 이미 설치했다면 반복하지 않는다. Git이 없으면 설치한다.

```sh
sudo apt update
sudo apt install -y git
sudo systemctl enable --now docker
```

현재 안내는 Docker 그룹 설정 없이 `sudo docker`를 사용하는 기준이다.

### 4. EC2에서 GitHub 코드 받기

**처음 받는 경우에만 EC2 터미널**에서 실행한다.

```sh
cd ~
git clone --branch feat-devops https://github.com/bae-bong/ai_mini_project.git
cd ~/ai_mini_project
git branch --show-current
git log -1 --oneline
ls -a
```

브랜치가 `feat-devops`이고 `compose.yaml`, `.env.example`, `frontend/`, `deploy/`가 있는지 확인한다. 이미 저장소가 있다면 다시 clone하지 말고 아래의 **기존 서버에 최신 코드 반영** 절차를 사용한다.

비공개 저장소에서 인증을 요구하면 GitHub 아이디를 입력하고 Password 칸에는 계정 비밀번호 대신 저장소 접근 권한이 있는 PAT를 입력한다. 토큰을 clone URL·명령어·채팅·저장소에 넣지 않는다. 403이면 계정의 저장소 접근 권한과 토큰 종류·권한을 확인한다. [GitHub 인증 안내](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

배포할 변경사항은 먼저 로컬에서 커밋·push되어 있어야 한다. 로컬에만 있는 수정은 EC2의 `git pull`로 받을 수 없다.

### 5. EC2용 환경변수 작성

**EC2 터미널**에서 실행한다. 이미 `.env`가 있으면 복사하지 않아 기존 값을 유지한다.

```sh
cd ~/ai_mini_project
test -f .env || cp .env.example .env
chmod 600 .env
nano .env
```

다음 세 값을 실제 값으로 채운다.

```dotenv
POSTGRES_PASSWORD=새로_생성한_DB_비밀번호
JWT_SECRET=별도로_생성한_JWT_비밀값
OPENAI_API_KEY=발급받은_OpenAI_API_키
```

- 새 DB의 비밀번호와 JWT 비밀값은 `openssl rand -hex 32`를 각각 한 번 실행해 서로 다른 값으로 생성한다. AWS에서 조회하는 값이 아니다.
- 기존 DB volume이 있다면 기존 비밀번호를 유지한다. `.env`만 바꿔도 DB 계정의 비밀번호가 바뀌지는 않는다.
- OpenAI 키는 [API Keys](https://platform.openai.com/api-keys)에서 발급한다.
- `HTTP_BIND_ADDRESS=127.0.0.1`, `HTTP_PORT=8080` 및 나머지 기본값은 유지한다.
- nano 저장: **Ctrl+O → 파일명 `.env` 확인 → Enter**, 종료: **Ctrl+X**.
- `.env`와 실제 비밀값은 커밋하거나 채팅에 붙이지 않는다.

설정을 검증한다. 아무 출력 없이 종료 코드 0이면 정상이다. `config --quiet`는 실제 비밀값을 출력하지 않는다.

```sh
sudo docker compose config --quiet
sudo docker compose config --services
```

서비스 목록에는 `db`, `nginx`, `auth`, `rag`가 있어야 한다. 두 개만 보이면 GitHub에 최신 통합 코드가 올라왔는지 확인한다.

### 6. EC2에서 전체 서비스 실행

**EC2 터미널**에서 실행한다. 처음에는 이미지 다운로드와 패키지 설치·빌드로 시간이 걸린다.

```sh
sudo docker compose up -d --build --wait
sudo docker compose ps
curl -i --max-time 10 http://127.0.0.1:8080/healthz
curl -i --max-time 10 http://127.0.0.1:8080/api/rag/health
```

확인 기준:

- `db`, `nginx`, `rag`: Up 상태와 healthy 표시
- `auth`: Up 상태 (현재 별도 healthcheck는 없음)
- 두 HTTP 요청: `200 OK`
- RAG 응답: `storage`가 `pgvector`. 자료를 아직 넣지 않았다면 `pdf_index`·`web_index`가 false여도 서버 기동 실패는 아니다.

새 DB volume에는 회원 테이블과 RAG 테이블이 자동 생성된다. **예전 설정으로 만든 DB volume이 있다면** 전체 실행 전 [기존 volume 갱신](#db-초기화와-기존-volume-갱신)을 수행한다. `down -v`로 DB를 지워 해결하지 않는다.

### 7. Mac 브라우저에서 접속

2번의 SSH 터미널을 유지한 채 **Mac 브라우저**에서 접속한다.

| 페이지    | 주소                             |
| --------- | -------------------------------- |
| 회원가입  | http://127.0.0.1:18080/signup    |
| 로그인    | http://127.0.0.1:18080/login     |
| AI 상담   | http://127.0.0.1:18080/chat      |
| 자료 관리 | http://127.0.0.1:18080/documents |

회원가입 → 로그인 → 기업 유형·직원 수 표시를 확인한다. 화면에는 경영지원/사업공고로 표시한다. 실제 질의응답은 관련 자료가 적재된 뒤 검증한다.

연결 확인이 필요하면 **Mac의 별도 새 터미널**에서 실행한다. 이 명령을 EC2 터미널에서 실행하지 않는다.

```sh
curl -i --max-time 10 http://127.0.0.1:18080/healthz
curl -i --max-time 10 http://127.0.0.1:18080/signup
```

현재 주소는 터널을 연 Mac에서만 접속할 수 있다. 다른 컴퓨터에서도 테스트하려면 그 컴퓨터에서 별도 SSH 터널을 열거나 공개 HTTPS 배포를 구성한다.

### 8. 상담 자료 적재

회원가입·로그인까지 확인했으면 아래 [법령 적재와 검색](#법령-적재와-검색)을 진행한다. GitHub에서 원본 파일을 받는 것만으로 DB 임베딩이 채워지지는 않는다. 이미 DB에 자료가 있으면 불필요하게 재적재하지 않는다. 기존 FAISS 자료가 있다면 [이관 절차](#기존-faiss-자료-이관)를 따른다.

## 기존 서버에 GitHub 최신 코드 반영

먼저 로컬에서 변경사항을 커밋·push한다. 아래는 **이미 SSH로 접속한 EC2 터미널**에서 실행한다.

```sh
cd ~/ai_mini_project
git status --short
git switch feat-devops
git pull --ff-only origin feat-devops
git log -1 --oneline
sudo docker compose config --quiet
sudo docker compose config --services
```

`git status`에 직접 수정한 파일이 있거나 pull이 실패하면 원인을 확인한다. `reset --hard`로 서버 파일을 덮어쓰지 않는다. `.env`는 Git에 없으므로 서버의 기존 값을 유지한다. SQL 변경이 있는 배포라면 먼저 백업하고 [기존 DB 갱신](#db-초기화와-기존-volume-갱신)을 적용한다.

**백엔드 코드·의존성도 변경된 경우**에는 다시 빌드한다.

```sh
sudo docker compose up -d --build --wait
sudo docker compose up -d --no-deps --no-build --force-recreate --wait nginx
```

**이번 필수 프론트엔드 화면만 반영하는 경우**에는 DB와 RAG가 실행 중인 상태에서 다음으로 충분하다. Nginx의 새 마운트·설정과 auth의 로그인 이동 주소를 적용한다.

```sh
sudo docker compose up -d --no-deps --no-build --force-recreate --wait nginx auth
```

둘 중 변경 범위에 맞는 한 가지를 실행한 다음 확인한다.

```sh
sudo docker compose ps
curl -i --max-time 10 http://127.0.0.1:8080/api/rag/health
```

Nginx 설정 파일은 `git pull`만으로 다시 로드되지 않는다. 위 Nginx 재생성으로 새 설정을 반영한다. 브라우저에 이전 화면이 보이면 Mac에서 새로고침한다.

## 다시 켜기와 중지

### EC2를 중지했다가 다시 켤 때

1. AWS 콘솔에서 인스턴스를 시작하고 현재 퍼블릭 주소를 확인한다.
2. 위 SSH 명령의 `EC2_HOST`를 갱신하고 터널을 연다.
3. **EC2 터미널**에서 기존 이미지를 사용해 서비스를 실행한다. 코드 변경이 없다면 재빌드하지 않는다.

```sh
cd ~/ai_mini_project
sudo systemctl start docker
sudo docker compose up -d --no-build --wait
sudo docker compose ps
```

### 서비스만 중지할 때

**EC2 터미널**에서 실행한다. 컨테이너와 DB volume은 유지한다.

```sh
cd ~/ai_mini_project
sudo docker compose stop
```

재개할 때는 위의 `up -d --no-build --wait`를 실행한다. EC2 자체를 중지하려면 AWS 콘솔에서 **인스턴스 중지**를 선택한다. 서버를 보존하려는 경우 **인스턴스 종료(terminate)**를 선택하지 않는다.

SSH 창을 닫으면 터널만 닫히고 `-d`로 실행한 서비스는 계속 동작한다. DB를 보존해야 하므로 `docker compose down -v`는 실행하지 않는다.

## 자주 발생하는 문제

| 증상                              | 확인·조치                                                                               |
| --------------------------------- | --------------------------------------------------------------------------------------- |
| `docker: command not found`       | EC2에 Docker 설치. Mac의 Docker 설치와 별개다.                                          |
| Docker socket `permission denied` | 이 안내대로 `sudo docker ...` 사용                                                      |
| GitHub 인증 실패·403              | 계정의 저장소 접근 권한과 PAT 확인                                                      |
| Compose에 db/nginx만 있음         | 최신 코드가 로컬에서 push됐는지 확인 후 EC2에서 pull                                    |
| `/api/rag/health`가 502           | `sudo docker compose ps -a`, `sudo docker compose logs --tail=100 rag nginx` 확인       |
| `/signup`이 Nginx 404             | 최신 코드 확인 후 Nginx를 위 명령으로 재생성                                            |
| Mac의 18080 연결 실패             | Mac에서 SSH 터널을 열었는지, 터널 창을 닫지 않았는지 확인                               |
| `Address already in use`          | 기존 터널을 사용하거나 다른 Mac 포트(예: 18081)로 터널과 브라우저 주소를 함께 변경      |
| DB 테이블 없음·RAG 503            | 기존 volume은 초기화 SQL이 재실행되지 않는다. DB 갱신 절차 확인                         |
| 로그인 성공 후 상담 불가          | 자료 적재 상태와 auth/rag의 JWT_SECRET 일치 여부 확인. 비밀값을 로그에 출력하지 않는다. |

로그 확인은 **EC2 터미널**에서 한다. 외부에 공유할 때 토큰·비밀번호·개인정보는 제거한다.

```sh
cd ~/ai_mini_project
sudo docker compose logs --tail=100 auth rag nginx
```

## DuckDNS 공개 HTTPS 연결

현재 도메인은 **skala-ai.duckdns.org**다. 2026-10-23 DNS 조회 시 도메인과 기존 EC2 퍼블릭 DNS가 모두 `32.236.191.133`을 가리키는 것을 확인했다. 이후 EC2 중지·시작으로 IP가 바뀌면 DuckDNS도 현재 IP로 갱신해야 한다. 아래 설정은 EC2에서 적용하기 전까지 외부에 공개되지 않는다.

`compose.public.yaml`은 공개 배포용 추가 설정이다. 기본 Compose의 DB·백엔드 비공개와 로컬 8080 접속을 유지하면서 Nginx의 80/443만 추가 공개한다. 인증서 발급 전에는 HTTP에서 로그인·상담을 제공하지 않는다.

### 1. AWS 보안 그룹 설정

EC2 콘솔에서 현재 인스턴스에 연결된 보안 그룹의 인바운드 규칙에 추가한다.

| 종류  | TCP 포트 | IPv4 소스             |
| ----- | -------- | --------------------- |
| HTTP  | 80       | `0.0.0.0/0`           |
| HTTPS | 443      | `0.0.0.0/0`           |
| SSH   | 22       | 관리자 공인 IP만 유지 |

8001·8080·5432는 외부에 열지 않는다. HTTP-01 인증서 발급·갱신에는 80 포트가 필요하므로 발급 후에도 유지한다. [Let's Encrypt 인증 방식](https://letsencrypt.org/docs/challenge-types/)

### 2. EC2에서 최신 설정과 Certbot 준비

로컬 변경사항을 커밋·push한 뒤 **EC2 터미널**에서 실행한다. 기존 auth/rag/db가 동작 중인 서버를 기준으로 한다.

```sh
cd ~/ai_mini_project
git pull --ff-only origin feat-devops
sudo apt update
sudo apt install -y certbot
sudo mkdir -p /var/www/certbot/.well-known/acme-challenge /etc/letsencrypt
getent ahostsv4 skala-ai.duckdns.org
```

주소가 현재 EC2 퍼블릭 IPv4와 일치해야 한다. 불필요한 AAAA 레코드가 있다면 실제 IPv6 접속도 가능한지 확인하거나 DuckDNS에서 정리한다. 이 절차에서는 DuckDNS 토큰이 필요하지 않다.

### 3. 인증서 발급용 HTTP만 시작

**EC2 터미널**에서 실행한다. `PUBLIC_NGINX_CONFIG`는 이 명령에만 적용하며 `.env`에 저장하지 않는다.

```sh
sudo env PUBLIC_NGINX_CONFIG=./deploy/nginx/domain-http.conf \
  docker compose -f compose.yaml -f compose.public.yaml \
  up -d --no-deps --no-build --force-recreate --wait nginx
printf '%s\n' 'duckdns-check' | sudo tee /var/www/certbot/.well-known/acme-challenge/duckdns-check
```

**Mac 또는 다른 외부 컴퓨터**에서 아래 주소를 확인한다. SSH 터널은 필요하지 않다.

```sh
curl -fsS --max-time 10 http://skala-ai.duckdns.org/.well-known/acme-challenge/duckdns-check
```

`duckdns-check`가 나오면 DNS·80 포트·인증용 파일 경로가 연결된 것이다. 연결 실패 시 DNS와 보안 그룹부터 확인하고 다음 단계로 넘어가지 않는다. 이 단계에서 일반 페이지의 HTTP 503은 HTTPS 준비 중 안내로 정상이다.

### 4. 인증서 발급

**EC2 터미널**에서 실행한다. 이메일·이용약관 등은 Certbot이 묻는 내용을 확인해 직접 입력한다. Docker Nginx를 사용하므로 호스트 Nginx를 수정하는 `certbot --nginx` 대신 webroot 방식을 쓴다.

```sh
sudo certbot certonly --webroot -w /var/www/certbot \
  --cert-name skala-ai.duckdns.org -d skala-ai.duckdns.org
```

성공 후 `/etc/letsencrypt/live/skala-ai.duckdns.org/`에 인증서가 있어야 한다. 개인키는 Git이나 채팅에 공유하지 않는다. 인증서 발급이 실패했다면 HTTPS 설정을 시작하지 않고 Certbot 오류부터 해결한다.

### 5. HTTPS 적용

**EC2 터미널**에서 실행한다. 이번에는 `PUBLIC_NGINX_CONFIG`를 지정하지 않아 기본 공개 HTTPS 설정이 선택된다.

```sh
sudo docker compose -f compose.yaml -f compose.public.yaml \
  up -d --no-deps --no-build --force-recreate --wait nginx
sudo docker compose -f compose.yaml -f compose.public.yaml exec -T nginx nginx -t
sudo docker compose -f compose.yaml -f compose.public.yaml ps
```

외부 컴퓨터에서 확인한다. 운영 확인에 `curl -k`로 인증서 검증을 생략하지 않는다.

```sh
curl -I --max-time 10 http://skala-ai.duckdns.org/login
curl -I --max-time 10 https://skala-ai.duckdns.org/login
curl -fsS --max-time 10 https://skala-ai.duckdns.org/api/rag/health
```

HTTP는 HTTPS로 308 이동하고, HTTPS 로그인 페이지와 RAG 상태 요청은 200이어야 한다. 브라우저에서 **https://skala-ai.duckdns.org/signup**으로 가입·로그인을 확인한다. HTTPS에서 발급하는 인증 쿠키에는 Nginx가 Secure 속성을 추가한다. 서버 `.env`나 DB·백엔드 코드를 이 도메인 때문에 변경할 필요는 없다.

### 6. 자동 갱신과 Nginx 재로드

**EC2 터미널**에서 갱신 후 실행할 hook을 설치한다. 프로젝트를 다른 경로에 clone했다면 아래 `cd`를 실제 경로로 바꾼다. 이 설정은 한 번만 설치한다.

```sh
sudo mkdir -p /etc/letsencrypt/renewal-hooks/deploy
sudo tee /etc/letsencrypt/renewal-hooks/deploy/10-reload-project-nginx.sh > /dev/null <<'HOOK'
#!/bin/sh
set -eu
cd /home/ubuntu/ai_mini_project
docker compose -f compose.yaml -f compose.public.yaml exec -T nginx nginx -t
docker compose -f compose.yaml -f compose.public.yaml exec -T nginx nginx -s reload
HOOK
sudo chmod 755 /etc/letsencrypt/renewal-hooks/deploy/10-reload-project-nginx.sh
sudo systemctl enable --now certbot.timer
sudo systemctl list-timers certbot.timer
sudo certbot renew --dry-run
sudo /etc/letsencrypt/renewal-hooks/deploy/10-reload-project-nginx.sh
```

Certbot은 호스트 인증서를 갱신하고 Nginx는 디렉터리 마운트를 통해 새 파일을 읽는다. 성공적인 갱신 후 hook이 Nginx를 재로드한다. timer·dry-run·hook 명령이 성공하는지 확인한다. [Certbot 갱신 안내](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates)

### 공개 모드에서 업데이트·재기동

이후에는 항상 기본 파일과 공개 파일을 함께 사용한다. 기본 파일만으로 Nginx를 재생성하면 SSH 전용 설정으로 되돌아간다.

```sh
cd ~/ai_mini_project
git pull --ff-only origin feat-devops
sudo docker compose -f compose.yaml -f compose.public.yaml up -d --build --wait
sudo docker compose -f compose.yaml -f compose.public.yaml \
  up -d --no-deps --no-build --force-recreate --wait nginx
```

코드 변경 없이 EC2만 다시 켰다면 다음으로 충분하다.

```sh
sudo docker compose -f compose.yaml -f compose.public.yaml up -d --no-build --wait
```

문제가 생겨 SSH 전용으로 되돌릴 때는 인증서와 DB를 삭제하지 않고 Nginx만 기본 설정으로 재생성한다.

```sh
sudo docker compose -f compose.yaml up -d --no-deps --no-build --force-recreate --wait nginx
```

공개 설정의 로컬 검증은 `python3 deploy/test_https.py`로 실행한다. 임시 자체서명 인증서와 임시 컨테이너만 사용하며 실제 인증서를 발급하지 않는다.

## Docker Engine·Compose 최초 설치

**EC2 Ubuntu 터미널**에서만 실행한다. 이미 Docker와 Compose가 설치되어 있으면 건너뛴다. Mac의 Docker 설치와 EC2의 설치는 별개다. 아래는 [Docker 공식 Ubuntu 설치 안내](https://docs.docker.com/engine/install/ubuntu/)의 apt 저장소 방식이다.

```sh
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
sudo docker compose version
```

`Hello from Docker!`와 Compose 버전이 나오면 설치 확인이 끝난다. 이후 프로젝트를 clone하고 서버 `.env`를 작성한다.

## DB 초기화와 기존 volume 갱신

빈 PostgreSQL volume에는 아래 SQL이 파일명 순서로 자동 적용된다.

1. `deploy/postgres/001-vector.sql`: vector 확장
2. `workplace/sql/init.sql`: 회원·기업 페르소나
3. `rag-code-template_ext/core/pgvector.sql`: 컬렉션·청크·벡터·메타데이터

기존 DB volume은 초기화 SQL을 다시 실행하지 않는다. 먼저 백업한 뒤 필요한 마이그레이션을 적용한다. 아래는 기존 volume에 이번 스키마를 추가하는 명령이다. `init.sql`은 이미 존재하는 테이블을 변경하지 않으므로, 기존 users의 페르소나 컬럼은 `alter_users.sql`도 적용한다.

```sh
sudo docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < workplace/sql/init.sql
sudo docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < workplace/sql/alter_users.sql
sudo docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < rag-code-template_ext/core/pgvector.sql
```

이 명령은 MariaDB 사용자 데이터를 자동 이관하지 않는다. 기존 DB의 버전·스키마·데이터는 별도로 확인해 옮긴다. `.env`의 비밀번호 변경만으로 기존 PostgreSQL 계정 비밀번호가 바뀌지는 않는다.

## 법령 적재와 검색

원본 PDF는 `upload-paper/`를 읽기 전용으로 마운트한다. 전처리 결과는 `law_processed` volume, 임베딩·메타데이터·모델·부칙은 PostgreSQL의 `rag_collections`, `rag_chunks`에 함께 저장한다. 실행에 FAISS 파일이나 pickle이 필요하지 않다.

```sh
sudo docker compose exec rag python -m law_pipeline preprocess
sudo docker compose exec rag python -m law_pipeline build
sudo docker compose exec rag python -m law_pipeline search '연차 유급휴가 발생 조건은?'
sudo docker compose exec rag python -m law_pipeline.evaluate
```

`build`는 OpenAI 임베딩 API를 호출한다. 다른 모델을 사용한다면 `--model` 및 필요 시 `--dimensions`를 `.env` 설정과 맞춘다. 기존 pdf 컬렉션을 재생성할 때는 백업 후 `build --replace`를 사용한다. 전체 청크·임베딩·모델·부칙을 하나의 DB 트랜잭션으로 교체하며, 실패하면 기존 데이터가 유지된다.

- 기존 청킹·미래 조문 제외·메타데이터 계약·BM25/RRF 검색을 유지한다.
- pgvector L2 거리를 제곱하여 기존 FAISS squared L2 점수 의미를 유지한다. 점수는 법적 확신도가 아니다.
- 현재 규모에서는 정확 검색을 사용한다. HNSW 등 근사 인덱스는 추가하지 않았다.
- 직접 `/upload-paper` 적재는 같은 파일명을 교체하고 다른 파일은 유지한다. 파이프라인이 관리하는 컬렉션에는 직접 업로드할 수 없다.
- 예전 CLI의 `--index` 옵션은 제거했다. DB 컬렉션이 저장·검색의 기준이다.

## 기존 FAISS 자료 이관

원본 FAISS 디렉터리를 별도 보관한다. 다음 명령은 신뢰하는 로컬 파일만 읽고, 기존 임베딩을 재사용해 PostgreSQL에 복사한다. 원본 파일은 삭제·수정하지 않는다. pickle을 읽으므로 외부에서 받은 미검증 파일에는 사용하지 않는다.

```sh
sudo docker compose run --rm --no-deps \
  -v "$PWD/rag-code-template_ext/faiss_db:/legacy:ro" \
  rag sh -c 'pip install -r requirements-migration.txt && python -m law_pipeline.migrate_faiss /legacy/pdf --trusted-local-files'
```

- `manifest.json`이 없으면 실제 기존 모델을 `--model`로 명시한다.
- 비어 있지 않은 `IndexFlatL2`만 지원한다. 기존 DB 컬렉션을 덮어쓰려면 백업 후 `--replace`를 명시한다.
- 법령 출처 메타데이터가 없는 오래된 인덱스는 자동 보충하지 않고 이관을 거절한다. 원본 PDF를 재적재한다.
- FAISS 의존성은 일회성 이관 컨테이너에만 설치하며, 운영 RAG 이미지에는 포함하지 않는다.
- 이관 후 `search`와 동일 평가셋을 비교하고 모델·차원·개수를 확인한다. 실제 임베딩 기반 품질 평가는 담당자가 실행해야 한다.

## 경로와 연결 규격

| 외부 경로          | 내부 경로                  |
| ------------------ | -------------------------- |
| `/api/auth/signup` | `auth:8080/api/signup`     |
| `/api/auth/login`  | `auth:8080/api/login`      |
| `/api/auth/logout` | `auth:8080/api/logout`     |
| `/api/auth/check`  | `auth:8080/api/auth/check` |
| `/api/rag/*`       | `rag:8001/*` (접두사 제거) |

`/login`, `/signup`, `/chat`, `/documents`는 Nginx가 새 정적 Vue 앱으로 제공한다. 기존 인증 서버의 `/static/*`, `/api/login` 등도 계속 전달한다. Java는 `DB_URL`, `DB_USER`, `DB_PASSWORD`, `JWT_SECRET`, `LOGIN_REDIRECT`를 받는다. RAG는 `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`와 동일한 `JWT_SECRET`을 받는다. 로컬 개발에서는 표준 PostgreSQL URI인 `RAG_DATABASE_URL`도 지원한다. DB 주소는 컨테이너 안에서 `localhost` 대신 `db`를 사용한다.

## 테스트

```sh
python3 deploy/smoke_test.py
```

별도 무작위 이름의 테스트 스택을 생성한다. 실제 인증·JWT 전달, pgvector 검색·모델·차원 검사, 교체 실패 롤백, 메타데이터·부칙·페르소나 응답, 기존 법령 PDF 회귀 검사, 컨테이너 재생성 후 데이터 보존을 검증한다. 임베딩과 LLM은 테스트 대역을 사용해 외부 AI API를 호출하지 않는다. 종료 시 해당 테스트 컨테이너·volume만 제거한다.

`RUN_DB_TESTS=1`은 폐기 가능한 DB에서만 사용한다. 통합 테스트는 테스트 컬렉션을 지운다. 일반 법령 파서 테스트는 DB 없이 실행할 수 있다.

```sh
cd rag-code-template_ext
python -m unittest discover -s law_pipeline/tests -v
```

## 백업·복구

```sh
mkdir -p backups
sudo docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backups/postgres-$(date +%Y%m%d-%H%M%S).dump"
```

종료 코드 0을 확인하고 사본을 EC2 밖에도 보관한다. 이 dump는 회원·청크·벡터·모델 설정·부칙을 함께 포함한다. 복원 검증은 별도 임시 DB에서 수행한다. 원본 PDF도 별도 보관한다. `docker compose down`은 named volume을 유지하고, `down -v`는 DB와 전처리 volume을 삭제하므로 운영에서 사용하지 않는다.

## 작업 기록

필수 페이지와 UI 변경 반영 방법은 [프론트엔드 안내](../frontend/README.md)에 있다. 프론트 파일과 Nginx 마운트·경로가 변경되면 Nginx 컨테이너를 재생성한다.

로컬에서 ARM64 이미지를 빌드해 EC2로 전송하는 방법은 [LOCAL_IMAGE_DEPLOY.md](LOCAL_IMAGE_DEPLOY.md)에 보류 항목으로 저장했다. 현재 배포 방식은 EC2 빌드로 유지한다.

변경 이유·영향 파일·검증 결과·남은 작업은 [docs/WORK_LOG.md](../docs/WORK_LOG.md)에 날짜별로 기록한다. 팀의 과거 평가 결과와 검토 문서는 당시 FAISS 결과로 남기고, 이번 변경의 안내는 [PGVECTOR_MIGRATION.md](../rag-code-template_ext/law_pipeline/PGVECTOR_MIGRATION.md)를 따른다.

```markdown
# 이후 EC2에서 업데이트할 때:

cd ~/ai_mini_project
git switch develop
git pull --ff-only origin develop

sudo docker compose -f compose.yaml -f compose.public.yaml \
 up -d --build --wait

sudo docker compose -f compose.yaml -f compose.public.yaml \
 up -d --no-deps --no-build --force-recreate --wait nginx
```
