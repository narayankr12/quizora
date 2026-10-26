#!/usr/bin/env python3
import cgi
import io
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pypdf import PdfReader

try:
    import psycopg
except ImportError:  # Local JSON fallback still works without PostgreSQL installed.
    psycopg = None


ROOT = Path(__file__).parent
HTML_FILE = ROOT / "pdf_quiz_site.html"
SAMPLE_FILE = ROOT / "java_mcq_quiz.html"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8002"))
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
VISITOR_FILE = DATA_DIR / "visitor_count.json"
USER_FILE = DATA_DIR / "user_names.json"
VISITOR_LOCK = threading.Lock()
INITIAL_TOTAL_VISITS = int(os.environ.get("INITIAL_TOTAL_VISITS", "0"))
PREVIOUS_USER_COUNT = int(os.environ.get("PREVIOUS_USER_COUNT", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_READY = False
DB_INIT_LOCK = threading.Lock()
ADMIN_PASSWORD = os.environ.get("QUIZ_ADMIN_PASSWORD", "").strip()
ADMIN_SESSIONS = set()
ADMIN_SESSION_LOCK = threading.Lock()


def clean_lines(text):
    return [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]


def parse_question_page(page_text, sequence_number, current_assignment):
    normalized = re.sub(r"\s+", " ", page_text).strip()
    normalized = re.sub(r"Q\s*U\s*E\s*S\s*T\s*I\s*O\s*N", "QUESTION", normalized, flags=re.I)
    assignment_match = re.search(r"Assignment\s+0*(\d+)", normalized, re.I)
    if assignment_match:
        current_assignment = int(assignment_match.group(1))

    question_match = re.search(r"Question\s+(\d+)\s*:?", normalized, re.I)
    if not question_match:
        return None, current_assignment

    answer_match = re.search(r"Correct\s+Answer:\s*([a-d])", normalized, re.I)
    if not answer_match:
        return None, current_assignment

    question_end = question_match.end()
    answer_start = answer_match.start()
    question_block = normalized[question_end:answer_start].strip()
    option_matches = list(re.finditer(r"(?:^|\s)([a-d])\.\s+", question_block, re.I))
    if len(option_matches) < 4:
        return None, current_assignment

    prompt = question_block[:option_matches[0].start(1)].strip()
    options = []
    for index, match in enumerate(option_matches[:4]):
        start = match.end()
        end = option_matches[index + 1].start(1) if index + 1 < 4 else len(question_block)
        options.append(question_block[start:end].strip())

    explanation = ""
    solution_match = re.search(r"Detailed\s+Solution:\s*(.*)", normalized, re.I)
    if solution_match:
        explanation = solution_match.group(1).strip()

    prompt = re.sub(r"^(?:NPTEL Online Certification Courses|Indian Institute of Technology Kharagpur|PROGRAMMING IN JAVA|Assignment\s+\d+|TYPE OF QUESTION: MCQ|Number of questions:.*?Total marks:.*?)\s*", "", prompt, flags=re.I)

    return {
        "id": sequence_number,
        "week": current_assignment,
        "assignment": current_assignment,
        "number": int(question_match.group(1)),
        "question": prompt,
        "options": options,
        "answer": ord(answer_match.group(1).lower()) - ord("a"),
        "explanation": explanation,
    }, current_assignment


def extract_questions(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    questions = []
    current_assignment = 1
    for page in reader.pages:
        text = page.extract_text() or ""
        question, current_assignment = parse_question_page(text, len(questions) + 1, current_assignment)
        if question:
            questions.append(question)
    return questions


def read_sample_questions():
    text = SAMPLE_FILE.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"const QUESTIONS = (\[.*?\]);\n\s*const TOTAL", text, re.S)
    return json.loads(match.group(1)) if match else []


def database_enabled():
    return bool(DATABASE_URL and psycopg is not None)


def initialize_database():
    global DB_READY
    if not database_enabled() or DB_READY:
        return
    with DB_INIT_LOCK:
        if DB_READY:
            return
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS quiz_users ("
                    "id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, submitted_at TIMESTAMPTZ NOT NULL)"
                )
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS quiz_stats ("
                    "key TEXT PRIMARY KEY, value BIGINT NOT NULL)"
                )
                cursor.execute("SELECT COUNT(*) FROM quiz_stats WHERE key = 'total'")
                if cursor.fetchone()[0] == 0:
                    total = INITIAL_TOTAL_VISITS
                    try:
                        local_state = json.loads(VISITOR_FILE.read_text(encoding="utf-8"))
                        if isinstance(local_state, dict) and local_state.get("schema") == 3:
                            total = int(local_state.get("total", total))
                    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
                        pass
                    cursor.execute("INSERT INTO quiz_stats (key, value) VALUES ('total', %s)", (total,))
                cursor.execute("SELECT COUNT(*) FROM quiz_users")
                if cursor.fetchone()[0] == 0:
                    try:
                        local_users = json.loads(USER_FILE.read_text(encoding="utf-8"))
                    except (FileNotFoundError, json.JSONDecodeError):
                        local_users = []
                    if isinstance(local_users, list):
                        for user in local_users:
                            if isinstance(user, dict) and str(user.get("name", "")).strip():
                                submitted_at = user.get("submitted_at") or datetime.now(timezone.utc).isoformat()
                                cursor.execute(
                                    "INSERT INTO quiz_users (name, submitted_at) VALUES (%s, %s)",
                                    (str(user["name"]).strip(), submitted_at),
                                )
        DB_READY = True


def _new_visit_state():
    return {"schema": 3, "total": INITIAL_TOTAL_VISITS}


def _load_visit_state():
    try:
        state = json.loads(VISITOR_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        state = None
    if not isinstance(state, dict) or state.get("schema") != 3:
        return _new_visit_state(), True
    return {"schema": 3, "total": int(state.get("total", INITIAL_TOTAL_VISITS))}, False


def _save_visit_state(state):
    temporary = VISITOR_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    os.replace(temporary, VISITOR_FILE)


def _public_visit_state(state):
    return {"visits": int(state["total"]), "total": int(state["total"])}


def record_visit():
    """Record only deliberate frontend visits, not Render health checks."""
    if database_enabled():
        initialize_database()
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE quiz_stats SET value = value + 1 WHERE key = 'total' RETURNING value")
                total = cursor.fetchone()[0]
        return {"visits": int(total), "total": int(total)}
    with VISITOR_LOCK:
        state, changed = _load_visit_state()
        state["total"] = int(state.get("total", INITIAL_TOTAL_VISITS)) + 1
        _save_visit_state(state)
        return _public_visit_state(state)


def read_visit_stats():
    if database_enabled():
        initialize_database()
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT value FROM quiz_stats WHERE key = 'total'")
                total = cursor.fetchone()[0]
        return {"visits": int(total), "total": int(total)}
    with VISITOR_LOCK:
        state, changed = _load_visit_state()
        if changed:
            _save_visit_state(state)
        return _public_visit_state(state)


def record_user_name(name):
    clean_name = " ".join(str(name).split()).strip()
    if not clean_name:
        raise ValueError("Please enter your name.")
    if len(clean_name) > 100:
        raise ValueError("Name must be 100 characters or fewer.")
    if database_enabled():
        initialize_database()
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO quiz_users (name, submitted_at) VALUES (%s, %s)",
                    (clean_name, datetime.now(timezone.utc)),
                )
        return clean_name
    with VISITOR_LOCK:
        try:
            users = json.loads(USER_FILE.read_text(encoding="utf-8"))
            if not isinstance(users, list):
                users = []
        except (FileNotFoundError, json.JSONDecodeError):
            users = []
        users.append({"name": clean_name, "submitted_at": datetime.now(timezone.utc).isoformat()})
        temporary = USER_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, USER_FILE)
    return clean_name


def read_user_names():
    if database_enabled():
        initialize_database()
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT name, submitted_at FROM quiz_users ORDER BY id")
                return [
                    {"name": name, "submitted_at": submitted_at.isoformat() if hasattr(submitted_at, "isoformat") else str(submitted_at)}
                    for name, submitted_at in cursor.fetchall()
                ]
    with VISITOR_LOCK:
        try:
            users = json.loads(USER_FILE.read_text(encoding="utf-8"))
            return users if isinstance(users, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []


def is_admin_request(handler):
    cookie_header = handler.headers.get("Cookie", "")
    cookies = {}
    for item in cookie_header.split(";"):
        if "=" in item:
            key, value = item.strip().split("=", 1)
            cookies[key] = value
    with ADMIN_SESSION_LOCK:
        return cookies.get("admin_session") in ADMIN_SESSIONS


def start_admin_session():
    token = secrets.token_urlsafe(32)
    with ADMIN_SESSION_LOCK:
        ADMIN_SESSIONS.add(token)
    return token


class QuizHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        clean_path = self.path.split("?", 1)[0]
        if clean_path in {"/visitor_count.json", "/user_names.json"}:
            self.send_error(404)
            return
        if clean_path == "/healthz":
            self.send_json({"ok": True})
            return
        if clean_path == "/":
            self.path = "/pdf_quiz_site.html"
        elif clean_path == "/admin":
            self.path = "/admin.html"
        elif clean_path == "/api/users":
            if not is_admin_request(self):
                self.send_json({"error": "Owner login required."}, 401)
                return
            users = read_user_names()
            self.send_json({"previous_count": PREVIOUS_USER_COUNT, "new_count": len(users), "count": len(users), "users": users})
            return
        elif clean_path == "/api/sample":
            self.send_json({"questions": read_sample_questions()})
            return
        elif clean_path == "/api/stats":
            self.send_json(read_visit_stats())
            return
        elif clean_path == "/api/visit":
            self.send_json(record_visit())
            return
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/admin/login":
            length = int(self.headers.get("Content-Length", "0"))
            if not ADMIN_PASSWORD:
                self.send_json({"error": "Owner dashboard is not configured. Set QUIZ_ADMIN_PASSWORD before starting the server."}, 503)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                password = str(payload.get("password", ""))
                if not secrets.compare_digest(password, ADMIN_PASSWORD):
                    self.send_json({"error": "Incorrect owner password."}, 401)
                    return
                token = start_admin_session()
                self.send_json({"ok": True}, headers={"Set-Cookie": f"admin_session={token}; HttpOnly; SameSite=Strict; Path=/"})
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                self.send_json({"error": "Invalid login request."}, 400)
            return
        if self.path == "/api/admin/logout":
            cookie_header = self.headers.get("Cookie", "")
            for item in cookie_header.split(";"):
                if item.strip().startswith("admin_session="):
                    token = item.strip().split("=", 1)[1]
                    with ADMIN_SESSION_LOCK:
                        ADMIN_SESSIONS.discard(token)
            self.send_json({"ok": True}, headers={"Set-Cookie": "admin_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"})
            return
        if self.path == "/api/register":
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                self.send_json({"error": "Please enter a valid name."}, 400)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                name = record_user_name(payload.get("name", ""))
                self.send_json({"ok": True, "name": name})
            except (ValueError, json.JSONDecodeError, AttributeError, UnicodeDecodeError) as exc:
                self.send_json({"error": str(exc) or "Please enter a valid name."}, 400)
            return
        if self.path != "/api/extract":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.send_json({"error": "Please upload a PDF smaller than 20 MB."}, 413)
            return
        try:
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type", "")},
            )
            file_item = form["pdf"]
            pdf_bytes = file_item.file.read()
            if not pdf_bytes[:5] == b"%PDF-":
                self.send_json({"error": "The uploaded file does not appear to be a PDF."}, 400)
                return
            questions = extract_questions(pdf_bytes)
            if not questions:
                self.send_json({"error": "No complete MCQs were found. Use a text-based PDF or add OCR support for scanned pages."}, 422)
                return
            self.send_json({"questions": questions, "filename": file_item.filename, "count": len(questions)})
        except Exception as exc:
            self.send_json({"error": f"Could not read this PDF: {exc}"}, 400)

    def send_json(self, payload, status=200, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def main():
    initialize_database()
    print(f"PDF Quiz website running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), QuizHandler).serve_forever()


if __name__ == "__main__":
    main()
