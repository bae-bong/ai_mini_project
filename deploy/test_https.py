"""Local HTTPS/bootstrap checks with a temporary self-signed certificate.

Never contacts Let's Encrypt or changes EC2/DNS. Requires Docker, curl, openssl.
"""
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = "skala-ai.duckdns.org"


def main():
    project = "ai-mini-tls-" + secrets.token_hex(4)
    compose = ["docker", "compose", "-p", project, "-f", "compose.yaml", "-f", "compose.public.yaml"]
    with tempfile.TemporaryDirectory(prefix="ai-mini-tls-") as tmp:
        base = Path(tmp)
        live = base / "certs" / "live" / DOMAIN
        live.mkdir(parents=True)
        challenge = base / "webroot" / ".well-known" / "acme-challenge"
        challenge.mkdir(parents=True)
        (challenge / "local-check").write_text("challenge-ok")
        env = {**os.environ, "POSTGRES_PASSWORD": "test-only", "JWT_SECRET": "test-only",
               "OPENAI_API_KEY": "sk-test-only", "HTTP_PORT": "0", "HTTP_BIND_ADDRESS": "127.0.0.1",
               "PUBLIC_BIND_ADDRESS": "127.0.0.1", "PUBLIC_HTTP_PORT": "0", "PUBLIC_HTTPS_PORT": "0",
               "LETSENCRYPT_DIR": str(base / "certs"), "ACME_WEBROOT": str(base / "webroot"),
               "PUBLIC_NGINX_CONFIG": "./deploy/nginx/domain-http.conf"}

        def run(*args):
            return subprocess.check_output(args, cwd=ROOT, env=env, text=True).strip()

        def port(number):
            return run(*compose, "port", "nginx", str(number)).splitlines()[0].rsplit(":", 1)[1]

        def http(path):
            return run("curl", "--silent", "--show-error", "--max-time", "5", "--noproxy", "*",
                       "-D", "-", "-H", "Host: " + DOMAIN, "http://127.0.0.1:" + port(80) + path)

        def https(path):
            number = port(443)
            return run("curl", "--silent", "--show-error", "--max-time", "5", "--noproxy", "*",
                       "--resolve", f"{DOMAIN}:{number}:127.0.0.1", "--cacert", str(live / "fullchain.pem"),
                       "-D", "-", f"https://{DOMAIN}:{number}{path}")

        try:
            subprocess.run(["openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "1",
                            "-subj", "/CN=" + DOMAIN, "-addext", "subjectAltName=DNS:" + DOMAIN,
                            "-keyout", str(live / "privkey.pem"), "-out", str(live / "fullchain.pem")],
                           check=True, capture_output=True)
            run(*compose, "config", "--quiet")
            run(*compose, "up", "-d", "--no-deps", "--no-build", "--wait", "nginx")
            assert "challenge-ok" in http("/.well-known/acme-challenge/local-check")
            assert "503 Service" in http("/login")
            assert "404 Not Found" in http("/.well-known/acme-challenge/missing")
            env["PUBLIC_NGINX_CONFIG"] = "./deploy/nginx/domain-https.conf"
            run(*compose, "up", "-d", "--no-deps", "--no-build", "--force-recreate", "--wait", "nginx")
            run(*compose, "exec", "-T", "nginx", "nginx", "-t")
            redirect = http("/login?test=1")
            assert "308 Permanent" in redirect and "https://" + DOMAIN + "/login?test=1" in redirect
            assert "challenge-ok" in http("/.well-known/acme-challenge/local-check")
            assert "200 OK" in https("/healthz")
            for page in ("/login", "/signup", "/chat", "/admin"):
                assert 'id="app"' in https(page)
            backend = "from http.server import BaseHTTPRequestHandler,HTTPServer\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.send_header('Set-Cookie','ACCESS_TOKEN=test; Path=/; HttpOnly');self.end_headers();self.wfile.write(b'{}')\nHTTPServer(('0.0.0.0',8080),H).serve_forever()"
            run("docker", "run", "-d", "--name", project + "-auth", "--network", project + "_default",
                "--network-alias", "auth", "python:3.11-slim", "python", "-c", backend)
            for attempt in range(20):
                reply = https("/api/auth/check")
                if "200 OK" in reply:
                    break
                time.sleep(0.25)
            cookies = [line for line in reply.lower().splitlines() if line.startswith("set-cookie:")]
            assert cookies and "secure" in cookies[0], reply
            run(*compose, "exec", "-T", "nginx", "nginx", "-s", "reload")
            print("PASS: ACME bootstrap, HTTPS redirect/certificate/pages, Secure cookies, nginx reload")
        finally:
            subprocess.run(["docker", "rm", "-f", project + "-auth"], capture_output=True)
            subprocess.run([*compose, "down", "-v"], cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
