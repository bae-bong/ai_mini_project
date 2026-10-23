"""Run with python3 deploy/smoke_test.py; requires a running Docker engine.

Creates and removes only its own randomly named test stack and database volume.
Backend stand-ins verify proxy routing without API keys or application changes.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "ai-mini-smoke-" + secrets.token_hex(4)
ENV = {**os.environ, "POSTGRES_PASSWORD": secrets.token_hex(24),
       "POSTGRES_DB": "smoke", "POSTGRES_USER": "smoke",
       "HTTP_BIND_ADDRESS": "127.0.0.1", "HTTP_PORT": "0"}
COMPOSE = ["docker", "compose", "-p", PROJECT]


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, env=ENV, text=True).strip()


def sql(statement):
    return run(*COMPOSE, "exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1",
               "-U", "smoke", "-d", "smoke", "-Atc", statement)


BACKEND = '''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, threading
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()
        result = json.dumps(dict(path=self.path, method=self.command, body=body)).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(result)
    do_POST = do_GET
threading.Thread(target=ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever, daemon=True).start()
ThreadingHTTPServer(('0.0.0.0', 8001), Handler).serve_forever()
'''


def main():
    try:
        run(*COMPOSE, "config", "--quiet")
        run(*COMPOSE, "up", "-d", "--wait", "--wait-timeout", "120")
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
        assert sql("SELECT extversion FROM pg_extension WHERE extname='vector'")
        assert sql("SELECT '[1,2,3]'::vector <-> '[1,2,3]'::vector") == "0"
        sql("CREATE TABLE smoke_persistence(id int); INSERT INTO smoke_persistence VALUES (42)")
        run(*COMPOSE, "up", "-d", "--force-recreate", "--wait", "db")
        assert sql("SELECT id FROM smoke_persistence") == "42"

        run("docker", "run", "-d", "--name", PROJECT + "-backend",
            "--network", PROJECT + "_default", "--network-alias", "auth",
            "--network-alias", "rag", "python:3.11-slim", "python", "-c", BACKEND)
        for attempt in range(30):
            try:
                request("/api/rag/health")
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt == 29:
                    raise
                time.sleep(0.5)
        for external, internal in [
            ("/api/auth/signup", "/api/signup"),
            ("/api/auth/login", "/api/login"),
            ("/api/auth/logout", "/api/logout"),
            ("/api/auth/check", "/api/auth/check"),
            ("/api/rag/rag-query?source=pdf", "/rag-query?source=pdf"),
            ("/api/rag/upload-paper", "/upload-paper"),
        ]:
            result = json.loads(request(external, b"proxy-test"))
            assert result == {"path": internal, "method": "POST", "body": "proxy-test"}, result
        print("PASS: static pages, nginx config, pgvector, DB persistence, proxy paths/query/body")
    finally:
        subprocess.run(["docker", "rm", "-f", PROJECT + "-backend"], capture_output=True)
        subprocess.run([*COMPOSE, "down", "-v"], cwd=ROOT, env=ENV, check=True)


if __name__ == "__main__":
    main()
