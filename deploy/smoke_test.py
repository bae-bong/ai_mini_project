"""Run with python3 deploy/smoke_test.py; requires a running Docker engine.

Creates and removes only its own randomly named test stack and database volume.
Runs real auth/RAG services with dummy secrets; tests never call an AI provider.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "ai-mini-smoke-" + secrets.token_hex(4)
ENV = {**os.environ, "POSTGRES_PASSWORD": secrets.token_hex(24),
       "POSTGRES_DB": "smoke", "POSTGRES_USER": "smoke",
       "HTTP_BIND_ADDRESS": "127.0.0.1", "HTTP_PORT": "0",
       "JWT_SECRET": secrets.token_hex(32), "OPENAI_API_KEY": "sk-test-no-provider-calls",
       "EMBEDDING_MODEL": "text-embedding-ada-002", "EMBEDDING_DIMENSIONS": ""}
COMPOSE = ["docker", "compose", "-p", PROJECT]


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, env=ENV, text=True).strip()


def sql(statement):
    return run(*COMPOSE, "exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1",
               "-U", "smoke", "-d", "smoke", "-Atc", statement)


def main():
    try:
        run(*COMPOSE, "config", "--quiet")
        run(*COMPOSE, "up", "-d", "--build", "--wait", "--wait-timeout", "120")
        run(*COMPOSE, "exec", "-T", "nginx", "nginx", "-t")
        address = run(*COMPOSE, "port", "nginx", "80")
        base = "http://" + address
        # Ignore workstation HTTP proxy settings for local requests.
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, body=None):
            with client.open(base + path, data=body, timeout=5) as response:
                return response.read().decode()

        assert request("/healthz") == "ok\n"
        assert "<html" in request("/").lower()
        assert "Vue" in request("/external_index.html")
        for path in ("/login", "/signup", "/chat", "/documents"):
            assert 'id="app"' in request(path)
        assert "createApp" in request("/assets/app.js")
        assert "Vue" in request("/assets/vendor/vue.global.prod.js")
        assert sql("SELECT extversion FROM pg_extension WHERE extname='vector'")
        assert sql("SELECT '[1,2,3]'::vector <-> '[1,2,3]'::vector") == "0"
        sql("CREATE TABLE smoke_persistence(id int); INSERT INTO smoke_persistence VALUES (42)")
        run(*COMPOSE, "up", "-d", "--force-recreate", "--wait", "db")
        assert sql("SELECT id FROM smoke_persistence") == "42"

        for attempt in range(30):
            try:
                assert json.loads(request("/api/rag/health"))["storage"] == "pgvector"
                request("/login")
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt == 29:
                    raise
                time.sleep(0.5)
        form = {"username": "smoke-user", "password": "test-only-password",
                "employees": "3", "companyTypes": "창업기업"}
        request("/api/auth/signup", urllib.parse.urlencode(form).encode())
        login = json.loads(request("/api/auth/login", urllib.parse.urlencode(form).encode()))
        assert login["redirect"] == "/chat"
        token = login["accessToken"]
        req = urllib.request.Request(base + "/api/rag/me", headers={"Authorization": "Bearer " + token})
        with client.open(req, timeout=5) as response:
            assert json.load(response)["persona"]["employees"] == 3
        req = urllib.request.Request(base + "/api/auth/check", headers={"Cookie": "ACCESS_TOKEN=" + token})
        with client.open(req, timeout=5) as response:
            assert json.load(response)["ok"]
        assert sql("SELECT employees FROM users WHERE username='smoke-user'") == "3"
        run(*COMPOSE, "exec", "-T", "-e", "RUN_DB_TESTS=1", "rag", "python", "-m", "unittest", "discover", "-s", "law_pipeline/tests", "-v")
        run(*COMPOSE, "exec", "-T", "-e", "RUN_DB_TESTS=1", "rag", "python", "-m", "unittest", "discover", "-s", "tests", "-v")
        # Optional FAISS is installed in this disposable container, not the production image.
        run(*COMPOSE, "run", "--rm", "--no-deps", "rag", "sh", "-c",
            "pip install --quiet -r requirements-migration.txt && "
            "RUN_DB_TESTS=1 python -m unittest discover -s tests -p test_faiss_migration.py -v")
        print("PASS: static pages, real auth/PostgreSQL, Java JWT -> RAG, pgvector, persistence, law/API tests")
    finally:
        subprocess.run([*COMPOSE, "down", "-v"], cwd=ROOT, env=ENV, check=True)


if __name__ == "__main__":
    main()
