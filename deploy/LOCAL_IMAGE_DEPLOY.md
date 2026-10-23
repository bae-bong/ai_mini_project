# 나중에 적용할 로컬 이미지 빌드·EC2 전송

상태: 보류. 현재는 EC2에서 Compose로 빌드한다. 아래 절차는 사용자가 전환할 때 실행하며, 이번 기록으로 빌드·전송 방식을 변경하지 않는다.

## Mac에서 빌드

EC2가 ARM64이므로 `linux/arm64`를 지정한다. 로컬 코드와 EC2의 Compose·마운트 파일은 같은 커밋으로 맞춘다. 아래 태그는 현재 Compose 프로젝트 이름 `ai-mini-project`의 기본 이미지 이름이다.

```sh
cd ~/ai_mini_project
docker buildx build --platform linux/arm64 --load \
  -t ai-mini-project-auth:latest ./workplace
docker buildx build --platform linux/arm64 --load \
  -t ai-mini-project-rag:latest ./rag-code-template_ext
docker save ai-mini-project-auth:latest ai-mini-project-rag:latest \
  | gzip > /tmp/ai-mini-images.tar.gz
```

기존 `.dockerignore`로 `.env`·비밀키를 빌드에서 제외한다. EC2의 실제 환경변수는 서버 `.env`에만 둔다. Nginx와 pgvector는 공식 이미지를 그대로 사용한다.

## Mac에서 전송

```sh
scp -i ~/.ssh/testkey.pem /tmp/ai-mini-images.tar.gz \
  ubuntu@ec2-32-236-191-133.ap-southeast-2.compute.amazonaws.com:~/
```

서버를 교체하면 호스트 주소와 키 경로를 맞춘다.

## EC2에서 로드·실행

진행 중인 기존 빌드가 있다면 먼저 정상 종료되었는지 확인한다. 같은 프로젝트의 배포 명령을 동시에 실행하지 않는다.

```sh
sudo docker load -i ~/ai-mini-images.tar.gz
cd ~/ai_mini_project
git pull --ff-only origin feat-devops
sudo docker compose config --quiet
sudo docker compose up -d --no-build --wait
sudo docker compose ps
curl -fsS http://127.0.0.1:8080/api/rag/health
```

`--no-build`로 EC2 재빌드를 방지한다. 이미지 전송은 DB 데이터를 이관하거나 스키마를 자동 변경하지 않는다. 필요한 DB 마이그레이션은 [배포 문서](README.md)에 따라 별도로 적용한다. 기존 named volume과 서버 `.env`를 유지한다.

반복 배포가 늘면 파일 전송 대신 ECR 등 레지스트리에서 버전 태그로 push/pull하는 방식으로 전환한다.

참고: [Docker save](https://docs.docker.com/reference/cli/docker/image/save/), [Docker load](https://docs.docker.com/reference/cli/docker/image/load/), [Compose up](https://docs.docker.com/reference/cli/docker/compose/up/).
