/*
##### The following practice code is intended for educational purposes only. For contact : architect@ajou.ac.kr, audit@korea.ac.kr 林圣烨 Ph.D.
#### This practice code is not a completed commercial version but has been developed for educational purposes; supplementation is required depending on the deployment objective for use as a commercial service.

```java

Guide for copying or moving the entire gradle directory (when receiving source code)
At this time, copy/move the entire folder containing gradlew, settings.gradle (or .kts), build.gradle (or .kts)

1. If ports such as 8080 (Java) or 8008 (FastAPI) are in use, disconnect them first
    lsof -iTCP:8080 -sTCP:LISTEN
    lsof -iTCP:8008 -sTCP:LISTEN
2. If a PID exists, terminate it
    kill -9 <PID>
3. In the new directory root, stop the Gradle Daemon
    ./gradlew --stop
    or pkill -f GradleDaemon || true
4. Completely delete local cache/build artifacts of the project
    rm -rf .gradle
    rm -rf build
    find . -name build -type d -prune -exec rm -rf {} +
*/
// or rm -rf **/build
/*
5. On first run, execute with cache disabled
    ./gradlew :app:clean --no-configuration-cache --no-build-cache
    ./gradlew :app:run   --no-configuration-cache --no-build-cache
6. Add the following to gradle.properties in the project root:
    org.gradle.configuration-cache=false
    org.gradle.caching=false

7. The users schema to be used by the authentication server must be created in advance (Ground Rule: table names must be written in lowercase)
    CREATE TABLE `users` (
    `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
    `username` varchar(100) NOT NULL,
    `password` varchar(255) NOT NULL,
    `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
    `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_users_username` (`username`)
    ) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    SELECT * FROM sql_db.users;
8. Run (e.g. from root directory workplace)
    workplace % ./gradlew build
    ./gradlew :app:run --no-configuration-cache 
*/

package workplace;

import com.auth0.jwt.JWT;
import com.auth0.jwt.JWTVerifier;
import com.auth0.jwt.algorithms.Algorithm;
import com.auth0.jwt.exceptions.JWTVerificationException;
import com.auth0.jwt.interfaces.DecodedJWT;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import workplace.SimpleAuthServer.AuthService;
import workplace.SimpleAuthServer.JdbcUserRepository;
import workplace.SimpleAuthServer.JwtUtil;
import workplace.SimpleAuthServer.User;
import workplace.SimpleAuthServer.UserRepository;

import org.mindrot.jbcrypt.BCrypt;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;

// java.sql – Specify only what is needed
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.SQLIntegrityConstraintViolationException;
import java.sql.Statement;

// java.time
import java.time.Instant;
import java.time.temporal.ChronoUnit;

// java.util – Specify only what is needed
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;

/*
Pure Java + HttpServer + MariaDB + Session + JWT Example

Modification history:
  1) Applied username/password trim on login/signup (prevents invalid_credentials caused by whitespace)
  2) Unified cookie creation/deletion attributes (cookie() helper)
     - Prevents missing cookie deletion or attribute mismatch on logout
  3) Added CORS + credentials support for /api/auth/check
  4) Maintained redirect to http://localhost:8008/index.html on successful login
  5) Added DEBUG logs to identify login failure causes
     - Outputs raw params / username/password / bcrypt matches result to console
 */

public class SimpleAuthServer {

    // ==== DB CONFIG (modify according to your environment) ==== //
    private static final String DB_URL = System.getenv().getOrDefault("DB_URL", "jdbc:mariadb://localhost:53301/sql_db");
    private static final String DB_USER = System.getenv().getOrDefault("DB_USER", "root");
    private static final String DB_PASSWORD = System.getenv("DB_PASSWORD");

    // ==== SESSION STORE ==== //
    private static final Map<String, User> SESSION_STORE = new ConcurrentHashMap<>();
    private static final String SESSION_COOKIE_NAME = "SESSION_ID";

    // ==== JWT COOKIE NAME ==== //
    private static final String JWT_COOKIE_NAME = "ACCESS_TOKEN";

    // ==== (Optional) Cookie name for login status check in Vue ==== //
    private static final String APP_AUTH_COOKIE_NAME = "APP_AUTH";

    // ==== CORS (dev) ==== //
    // In case frontend (8008) calls Java API (8080)
    private static final String DEV_FRONTEND_ORIGIN = "http://localhost:8008";
    private static final String LOGIN_REDIRECT =
            System.getenv().getOrDefault("LOGIN_REDIRECT", "http://localhost:8008/index.html");

    // ==== DEBUG ==== //
    private static final boolean DEBUG = true;

    public static void main(String[] args) throws Exception {
        // Load JDBC Driver
        Class.forName(DB_URL.startsWith("jdbc:postgresql")
                ? "org.postgresql.Driver"
                : "org.mariadb.jdbc.Driver");

        System.out.println("=== SimpleAuthServer v2026-02-03-REPLACED ===");

        // 필수 환경변수 확인: 빠지면 로그인 시점이 아니라 시작 시점에 바로 알린다
        java.util.List<String> missing = new java.util.ArrayList<>();
        for (String key : new String[]{"JWT_SECRET", "DB_PASSWORD"}) {
            String v = System.getenv(key);
            if (v == null || v.isBlank()) missing.add(key);
        }
        if (!missing.isEmpty()) {
            System.err.println("[CONFIG] 필수 환경변수가 없습니다: " + missing);
            System.err.println("[CONFIG] 예) export JWT_SECRET=... ; export DB_PASSWORD=...");
            System.exit(1);
        }
        System.out.println("[CONFIG] DB_URL=" + DB_URL + ", DB_USER=" + DB_USER);
        System.out.println("Server started at http://localhost:8080");

        //  어떤 계층이 DB 접근을 담당해야 하는가? Repository 구현체를 생성하여 서비스와 연결하세요.
        UserRepository userRepository = new JdbcUserRepository(DB_URL, DB_USER, DB_PASSWORD);
        //  HTTP 처리부가 SQL을 직접 실행하지 않도록 인증 서비스에 저장소를 주입하세요.
        AuthService authService = new AuthService(userRepository);

        HttpServer server = HttpServer.create(new InetSocketAddress(8080), 0);
        server.setExecutor(Executors.newFixedThreadPool(10));

        // GET / -> /login redirect
        server.createContext("/", exchange -> {
            if ("GET".equalsIgnoreCase(exchange.getRequestMethod())) {
                redirect(exchange, "/login");
            } else {
                methodNotAllowed(exchange);
            }
        });

        // GET /login -> login.html
        server.createContext("/login", exchange -> {
            if ("GET".equalsIgnoreCase(exchange.getRequestMethod())) {
                serveResource(exchange, "web/login.html", "text/html; charset=utf-8");
            } else {
                methodNotAllowed(exchange);
            }
        });

        // GET /signup -> signup.html
        server.createContext("/signup", exchange -> {
            if ("GET".equalsIgnoreCase(exchange.getRequestMethod())) {
                serveResource(exchange, "web/signup.html", "text/html; charset=utf-8");
            } else {
                methodNotAllowed(exchange);
            }
        });

        // GET /static/* -> CSS/JS
        server.createContext("/static", exchange -> {
            if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
                methodNotAllowed(exchange);
                return;
            }
            String path = exchange.getRequestURI().getPath(); // /static/style.css
            String filename = path.replaceFirst("/static/?", "");
            if (filename.isEmpty()) {
                notFound(exchange);
                return;
            }

            String resourcePath = "web/" + filename;
            String contentType;
            if (filename.endsWith(".css")) {
                contentType = "text/css; charset=utf-8";
            } else if (filename.endsWith(".js")) {
                contentType = "application/javascript; charset=utf-8";
            } else {
                contentType = "application/octet-stream";
            }
            serveResource(exchange, resourcePath, contentType);
        });

        // POST /api/signup -> Sign up
        server.createContext("/api/signup", exchange -> {
            if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
                methodNotAllowed(exchange);
                return;
            }
            Map<String, String> params = parseFormBody(exchange);

            String username = trimToNull(params.get("username"));
            String password = params.get("password");
            if (password != null) password = password.trim();

            if (DEBUG) {
                System.out.println("=== SIGNUP HIT ===");
                System.out.println("[SIGNUP] request received (credentials omitted)");
                System.out.println("[SIGNUP] username=" + username + ", pwLen=" + (password == null ? "null" : password.length()));
            }

            if (isBlank(username) || isBlank(password)) {
                writeText(exchange, 400, "username and password are required");
                return;
            }

            // 페르소나: 직원 수(필수), 기업 유형(선택, 쉼표 구분)
            Integer employees;
            try {
                employees = Integer.parseInt(trimToNull(params.get("employees")));
                if (employees < 0) throw new NumberFormatException();
            } catch (Exception e) {
                writeText(exchange, 400, "employees must be a non-negative number");
                return;
            }
            String companyTypes = trimToNull(params.get("companyTypes"));

            try {
                //  회원가입 요청을 어떤 계층으로 위임해야 하는가?
                authService.signUp(username, password, companyTypes, employees);redirect(exchange, "/login");
            } catch (SQLIntegrityConstraintViolationException dup) {
                writeText(exchange, 400, "username already exists");
            } catch (Exception e) {
                e.printStackTrace();
                writeText(exchange, 500, "signup error");
            }
        });

        // POST /api/login
        // Login + Session + JWT issuance + (Important) No 302 redirect → Return 200 JSON
        server.createContext("/api/login", exchange -> {
            if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
                methodNotAllowed(exchange);
                return;
            }

            try {
                Map<String, String> params = parseFormBody(exchange);
                String username = params.get("username");
                String password = params.get("password");

                System.out.println("[LOGIN] content-type=" + exchange.getRequestHeaders().getFirst("Content-Type"));
                System.out.println("[LOGIN] username(trim)=" + (username == null ? "null" : username.trim())
                        + ", pwLen=" + (password == null ? -1 : password.length()));

                if (isBlank(username) || isBlank(password)) {
                    writeJson(exchange, 400, "{\"ok\":false,\"error\":\"username and password are required\"}");
                    return;
                }

                // [① 로그인 연결] 서비스의 로그인 결과를 받아 실패 시 401, 성공 시 쿠키 발급으로 이어지게 하세요.
                User user = authService.login(username, password);
                if (user == null) {
                    writeJson(exchange, 401, "{\"ok\":false,\"error\":\"invalid credentials\"}");
                    return;
                }

                // 1) Session Cookie
                String sessionId = UUID.randomUUID().toString();
                //  서버 측 세션 상태를 어떤 키로 보관해야 이후 쿠키를 통해 찾을 수 있는가?
                SESSION_STORE.put(sessionId, user);exchange.getResponseHeaders().add(
                        "Set-Cookie",
                        SESSION_COOKIE_NAME + "=" + sessionId + "; Path=/; HttpOnly; SameSite=Lax"
                );

                // 2) JWT Cookie
                //  서버 측 세션과 별개로 검증 가능한 토큰을 발급하세요.
                String jwt = JwtUtil.createToken(user);
                exchange.getResponseHeaders().add(
                        "Set-Cookie",
                        JWT_COOKIE_NAME + "=" + jwt + "; Path=/; HttpOnly; SameSite=Lax"
                );

                // 3) Cookie for frontend login status check (readable by JS)
                exchange.getResponseHeaders().add(
                        "Set-Cookie",
                        APP_AUTH_COOKIE_NAME + "=1; Path=/; SameSite=Lax"
                );

                // 4) (Most important) Do not redirect here — end with 200 JSON response
                //    → In fetch/XHR environments, 302 redirects easily cause HTTP 0/cancellation
                //  fetch 호출에서는 서버 302 대신 성공 JSON으로 이동 경로를 알립니다.
                writeJson(exchange, 200, "{\"ok\":true,\"accessToken\":\"" + jwt + "\",\"redirect\":\"" + LOGIN_REDIRECT + "\"}");System.out.println("[LOGIN] SUCCESS -> 200 JSON (no redirect)");
                return;

            } catch (Exception e) {
                e.printStackTrace();
                // Respond with JSON so fetch receives 500 instead of HTTP 0
                writeJson(exchange, 500, "{\"ok\":false,\"error\":\"login error\"}");
                return;
            }
        });

        // GET /api/auth/check -> // Check login status (Session or JWT)
        server.createContext("/api/auth/check", exchange -> {
            // CORS Preflight
            if ("OPTIONS".equalsIgnoreCase(exchange.getRequestMethod())) {
                //  브라우저의 사전 요청은 인증 전에 CORS 헤더를 붙이고 본문 없이 종료해야 합니다.
                addCorsHeaders(exchange);exchange.sendResponseHeaders(204, -1);
                exchange.close();
                return;
            }

            if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
                methodNotAllowed(exchange);
                return;
            }

            addCorsHeaders(exchange);

            // (A) Session validation
            String sessionId = getCookieValue(exchange, SESSION_COOKIE_NAME);
            User sessionUser = null;
            if (!isBlank(sessionId)) {
                // [② 세션 검증] 쿠키의 세션 ID는 사용자 자체가 아닙니다. 서버 저장소에서 해당 사용자를 조회하세요.
                sessionUser = SESSION_STORE.get(sessionId);
            }

            // (B) JWT validation
            String token = getCookieValue(exchange, JWT_COOKIE_NAME);
            boolean jwtOk = false;
            String jwtUsername = null;
            String jwtSubject = null;

            if (!isBlank(token)) {
                try {
                    // [③ JWT 검증] 토큰 내용을 단순 디코딩하지 말고 서명·만료·발급자 검증 함수를 사용하세요.
                    DecodedJWT decoded = JwtUtil.verifyToken(token);
                    jwtOk = true;
                    jwtUsername = decoded.getClaim("username").asString();
                    jwtSubject = decoded.getSubject();
                } catch (JWTVerificationException ex) {
                    jwtOk = false;
                } catch (Exception ex) {
                    jwtOk = false;
                }
            }

            // Policy: pass if either session OR JWT is valid
            // [④ 인증 정책] Session OR JWT: 두 방식 모두 실패한 경우에만 거부하도록 조건을 작성하세요.
            if (sessionUser == null && !jwtOk) {
                writeJson(exchange, 401, "{\"ok\":false,\"reason\":\"UNAUTHORIZED\"}");
                return;
            }

            if (sessionUser != null) {
                String body = "{\"ok\":true,\"via\":\"session\",\"username\":\""
                        + escapeJson(sessionUser.getUsername()) + "\"}";
                writeJson(exchange, 200, body);
            } else {
                String body = "{\"ok\":true,\"via\":\"jwt\",\"username\":\""
                        + escapeJson(jwtUsername) + "\",\"sub\":\""
                        + escapeJson(jwtSubject) + "\"}";
                writeJson(exchange, 200, body);
            }
        });

        // (Optional) GET /home -> for session testing within 8080
        server.createContext("/home", exchange -> {
            if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
                methodNotAllowed(exchange);
                return;
            }
            User user = resolveUser(exchange);
            if (user == null) {
                redirect(exchange, "/login");
                return;
            }
            String body = """
                    <html>
                    <head>
                      <meta charset='utf-8'/>
                      <link rel='stylesheet' href='/static/style.css'/>
                      <title>Home</title>
                    </head>
                    <body>
                      <div class='container'>
                        <h1>Welcome, %s</h1>
                        <p>This is the /home page of the 8080 server.</p>
                        <form method='POST' action='/api/logout'>
                          <button type='submit'>Logout</button>
                        </form>
                      </div>
                    </body>
                    </html>
                    """.formatted(escapeHtml(user.getUsername()));
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "text/html; charset=utf-8");
            exchange.sendResponseHeaders(200, bytes.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(bytes);
            }
        });

        // POST /api/logout -> Log out
        server.createContext("/api/logout", exchange -> {
            if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
                methodNotAllowed(exchange);
                return;
            }

            String sessionId = resolveSessionId(exchange);
            if (sessionId != null) {
                //  로그아웃 때 서버 측 세션을 무효화하세요.
                SESSION_STORE.remove(sessionId);}

            // Remove cookie (delete with identical attributes)
            exchange.getResponseHeaders().add("Set-Cookie", cookie(SESSION_COOKIE_NAME, "", true, 0));
            exchange.getResponseHeaders().add("Set-Cookie", cookie(JWT_COOKIE_NAME, "", true, 0));
            exchange.getResponseHeaders().add("Set-Cookie", cookie(APP_AUTH_COOKIE_NAME, "", false, 0));

            redirect(exchange, "/login");
        });

        server.start();
    }

    // ========= CORS =========

    private static void addCorsHeaders(HttpExchange exchange) {
        exchange.getResponseHeaders().set("Access-Control-Allow-Origin", DEV_FRONTEND_ORIGIN);
        exchange.getResponseHeaders().set("Access-Control-Allow-Credentials", "true");
        exchange.getResponseHeaders().set("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
        exchange.getResponseHeaders().set("Access-Control-Allow-Headers", "Content-Type");
        exchange.getResponseHeaders().set("Vary", "Origin");
    }

    // ========= Resource/Reponse Helper =========

    private static void serveResource(HttpExchange exchange, String resourcePath, String contentType) throws IOException {
        InputStream is = SimpleAuthServer.class.getClassLoader().getResourceAsStream(resourcePath);
        if (is == null) {
            notFound(exchange);
            return;
        }
        byte[] bytes = is.readAllBytes();
        exchange.getResponseHeaders().add("Content-Type", contentType);
        exchange.sendResponseHeaders(200, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }

    private static void writeText(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "text/plain; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }

    private static void writeJson(HttpExchange exchange, int status, String json) throws IOException {
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }

    private static void redirect(HttpExchange exchange, String location) throws IOException {
        exchange.getResponseHeaders().add("Location", location);
        exchange.sendResponseHeaders(302, -1);
        exchange.close();
    }

    private static void methodNotAllowed(HttpExchange exchange) throws IOException {
        writeText(exchange, 405, "Method Not Allowed");
    }

    private static void notFound(HttpExchange exchange) throws IOException {
        writeText(exchange, 404, "Not Found");
    }

    private static Map<String, String> parseFormBody(HttpExchange exchange) throws IOException {
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        Map<String, String> params = new HashMap<>();
        for (String pair : body.split("&")) {
            if (pair.isEmpty()) continue;
            String[] kv = pair.split("=", 2);
            String key = urlDecode(kv[0]);
            String value = kv.length > 1 ? urlDecode(kv[1]) : "";
            params.merge(key, value, (a, b) -> a + "," + b);
        }
        return params;
    }

    private static String urlDecode(String s) {
        return URLDecoder.decode(s, StandardCharsets.UTF_8);
    }

    private static boolean isBlank(String s) {
        return s == null || s.trim().isEmpty();
    }

    private static String trimToNull(String s) {
        if (s == null) return null;
        String t = s.trim();
        return t.isEmpty() ? null : t;
    }

// Read common cookie
    private static String getCookieValue(HttpExchange exchange, String name) {
        List<String> cookies = exchange.getRequestHeaders().get("Cookie");
        if (cookies == null) return null;
        for (String header : cookies) {
            String[] parts = header.split(";\\s*");
            for (String part : parts) {
                String[] kv = part.split("=", 2);
                if (kv.length == 2 && name.equals(kv[0])) {
                    return kv[1];
                }
            }
        }
        return null;
    }

    private static String resolveSessionId(HttpExchange exchange) {
        return getCookieValue(exchange, SESSION_COOKIE_NAME);
    }

    private static User resolveUser(HttpExchange exchange) {
        String sessionId = resolveSessionId(exchange);
        if (sessionId == null) return null;
        return SESSION_STORE.get(sessionId);
    }

    private static String escapeHtml(String s) {
        if (s == null) return "";
        return s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&#39;");
    }

    private static String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

  // Cookie creation helper (unified attributes)
    // maxAgeSeconds:
    //  -1 : Max-Age not set (behaves like a session cookie)
    //   0 : Delete immediately
    
    private static String cookie(String name, String value, boolean httpOnly, int maxAgeSeconds) {
        StringBuilder sb = new StringBuilder();
        sb.append(name).append("=").append(value == null ? "" : value);
        sb.append("; Path=/");
        if (maxAgeSeconds >= 0) sb.append("; Max-Age=").append(maxAgeSeconds);
        if (httpOnly) sb.append("; HttpOnly");
        sb.append("; SameSite=Lax");
        return sb.toString();
    }

    // ========= Domain/ Repository / Service =========

    public static class User {
        private Long id;
        private String username;
        private String password;

        public Long getId() { return id; }
        public void setId(Long id) { this.id = id; }

        public String getUsername() { return username; }
        public void setUsername(String username) { this.username = username; }

        public String getPassword() { return password; }
        public void setPassword(String password) { this.password = password; }

        private String companyTypes;   // 쉼표 구분 (예: 여성기업,창업기업)
        private Integer employees;     // 상시근로자 수

        public String getCompanyTypes() { return companyTypes; }
        public void setCompanyTypes(String companyTypes) { this.companyTypes = companyTypes; }

        public Integer getEmployees() { return employees; }
        public void setEmployees(Integer employees) { this.employees = employees; }
    }

    public interface UserRepository {
        void save(User user) throws Exception;
        User findByUsername(String username) throws Exception;
    }

    public static class JdbcUserRepository implements UserRepository {
        private final String url;
        private final String user;
        private final String password;

        public JdbcUserRepository(String url, String user, String password) {
            this.url = url;
            this.user = user;
            this.password = password;
        }

        private Connection getConnection() throws SQLException {
            return DriverManager.getConnection(url, user, password);
        }

        @Override
        public void save(User u) throws Exception {
            String sql = "INSERT INTO users(username, password, company_types, employees) VALUES(?, ?, ?, ?)";
            try (Connection conn = getConnection();
                 PreparedStatement ps = conn.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
                //  SQL의 ? 자리와 사용자 필드를 연결하세요(문자열 결합 SQL 금지).
                ps.setString(1, u.getUsername());ps.setString(2, u.getPassword());
                ps.setString(3, u.getCompanyTypes());
                if (u.getEmployees() == null) ps.setNull(4, java.sql.Types.INTEGER); else ps.setInt(4, u.getEmployees());
                ps.executeUpdate();
                try (ResultSet rs = ps.getGeneratedKeys()) {
                    if (rs.next()) {
                        u.setId(rs.getLong(1));
                    }
                }
            }
        }

        @Override
        public User findByUsername(String username) throws Exception {
            String sql = "SELECT id, username, password, company_types, employees FROM users WHERE username = ?";
            try (Connection conn = getConnection();
                 PreparedStatement ps = conn.prepareStatement(sql)) {
                ps.setString(1, username);
                try (ResultSet rs = ps.executeQuery()) {
                    if (rs.next()) {
                        User u = new User();
                        u.setId(rs.getLong("id"));
                        u.setUsername(rs.getString("username"));
                        u.setPassword(rs.getString("password"));
                        u.setCompanyTypes(rs.getString("company_types"));
                        int emp = rs.getInt("employees");
                        u.setEmployees(rs.wasNull() ? null : emp);
                        return u;
                    }
                }
            }
            return null;
        }
    }

    public static class AuthService {
        private final UserRepository userRepository;

        public AuthService(UserRepository userRepository) {
            this.userRepository = userRepository;
        }

        public void signUp(String username, String rawPassword,
                           String companyTypes, Integer employees) throws Exception {
            username = trimToNull(username);
            if (rawPassword != null) rawPassword = rawPassword.trim();

            User existing = userRepository.findByUsername(username);
            if (existing != null) {
                throw new SQLIntegrityConstraintViolationException("username already exists");
            }

            // [⑤-A 비밀번호 저장] 평문 대신 cost 12의 BCrypt salt 포함 해시를 저장하세요.
            String hashed = BCrypt.hashpw(rawPassword, BCrypt.gensalt(12));

            User u = new User();
            u.setUsername(username);
            u.setPassword(hashed);
            u.setCompanyTypes(companyTypes);
            u.setEmployees(employees);
            userRepository.save(u);
        }

        public User login(String username, String rawPassword) throws Exception {
            username = trimToNull(username);
            if (rawPassword != null) rawPassword = rawPassword.trim();

            User u = userRepository.findByUsername(username);
            if (u == null) {
                if (DEBUG) System.out.println("[AUTH] user not found: " + username);
                return null;
            }
            if (DEBUG) {
                System.out.println("[AUTH] user found: " + username);
                String hash = u.getPassword();
                System.out.println("[AUTH] dbHash prefix: " + (hash == null ? "null" : hash.substring(0, Math.min(7, hash.length()))));
            }

            // [⑤-B 비밀번호 검증] 입력 평문과 저장된 BCrypt 해시를 검증하세요. ⑤-A와 한 묶음입니다.
            boolean ok = BCrypt.checkpw(rawPassword, u.getPassword());
            if (DEBUG) System.out.println("[AUTH] bcrypt matches=" + ok);

            if (!ok) return null;
            return u;
        }
    }

    // ========= JWT Util =========

    public static class JwtUtil {
        private static final String SECRET = System.getenv("JWT_SECRET");
        private static final Algorithm ALG = Algorithm.HMAC256(SECRET);
        private static final String ISSUER = "simple-auth-server";

        public static String createToken(User user) {
            Instant now = Instant.now();
            return JWT.create()
                    .withIssuer(ISSUER)
                    .withIssuedAt(Date.from(now))
                    //  JWT가 무기한 유효하지 않도록 발급 시점 기준 1시간 만료를 지정하세요.
                    .withExpiresAt(Date.from(now.plus(1, ChronoUnit.HOURS))).withSubject(String.valueOf(user.getId()))
                    .withClaim("username", user.getUsername())
                    .withClaim("companyTypes", user.getCompanyTypes() == null
                            ? java.util.List.<String>of()
                            : java.util.Arrays.asList(user.getCompanyTypes().split(",")))
                    .withClaim("employees", user.getEmployees())
                    .sign(ALG);
        }

        public static DecodedJWT verifyToken(String token) throws JWTVerificationException {
            JWTVerifier verifier = JWT.require(ALG)
                    .withIssuer(ISSUER)
                    .build();
            //  JWT 파싱만 하지 말고 검증기의 검증 결과를 반환하세요.
            return verifier.verify(token);
        }
    }
}
