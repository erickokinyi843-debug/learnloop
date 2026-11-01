from __future__ import annotations

import hashlib
import hmac
import http.cookies
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
CURRICULUM_CONTENT_FILE = ROOT / "curriculum_content.json"
DATA_DIR = Path(os.environ.get("LEARNLOOP_DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE = DATA_DIR / "learnloop.sqlite3"
HOST = os.environ.get("LEARNLOOP_HOST", "0.0.0.0")
PORT = int(os.environ.get("LEARNLOOP_PORT", os.environ.get("PORT", "8000")))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
ADMIN_SETUP_KEY = os.environ.get("ADMIN_SETUP_KEY", "")
CORS_ALLOWED_ORIGINS = frozenset(
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
)
SESSION_DAYS = 7
MAX_REQUEST_BYTES = 22 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".txt"}
PBKDF2_ITERATIONS = 310_000
SESSION_COOKIE = "learnloop_session"
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_ATTEMPTS_LOCK = threading.Lock()
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    (
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; connect-src 'self'; frame-src https://www.youtube.com "
        "https://www.youtube-nocookie.com https://player.vimeo.com; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
    ),
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CBC_GRADES = [
    "PP1", "PP2", "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5",
    "Grade 6", "Grade 7", "Grade 8", "Grade 9", "Grade 10", "Grade 11", "Grade 12",
]
CBC_SUBJECTS = {
    grade: subjects
    for grade, subjects in {
        "PP1": ["Language Activities", "Mathematical Activities", "Environmental Activities", "Psychomotor and Creative Activities", "Religious Education"],
        "PP2": ["Language Activities", "Mathematical Activities", "Environmental Activities", "Psychomotor and Creative Activities", "Religious Education"],
        "Grade 1": ["English", "Kiswahili", "Mathematics", "Environmental Activities", "Creative Activities", "Religious Education"],
        "Grade 2": ["English", "Kiswahili", "Mathematics", "Environmental Activities", "Creative Activities", "Religious Education"],
        "Grade 3": ["English", "Kiswahili", "Mathematics", "Environmental Activities", "Creative Activities", "Religious Education"],
        "Grade 4": ["English", "Kiswahili", "Mathematics", "Science and Technology", "Social Studies", "Agriculture", "Creative Arts", "Physical and Health Education", "Religious Education", "Indigenous Language"],
        "Grade 5": ["English", "Kiswahili", "Mathematics", "Science and Technology", "Social Studies", "Agriculture", "Creative Arts", "Physical and Health Education", "Religious Education", "Indigenous Language"],
        "Grade 6": ["English", "Kiswahili", "Mathematics", "Science and Technology", "Social Studies", "Agriculture", "Creative Arts", "Physical and Health Education", "Religious Education", "Indigenous Language"],
        "Grade 7": ["English", "Kiswahili", "Mathematics", "Integrated Science", "Health Education", "Pre-technical Studies", "Social Studies", "Business Studies", "Agriculture", "Life Skills Education", "Physical Education", "Creative Arts and Sports", "Religious Education"],
        "Grade 8": ["English", "Kiswahili", "Mathematics", "Integrated Science", "Health Education", "Pre-technical Studies", "Social Studies", "Business Studies", "Agriculture", "Life Skills Education", "Physical Education", "Creative Arts and Sports", "Religious Education"],
        "Grade 9": ["English", "Kiswahili", "Mathematics", "Integrated Science", "Health Education", "Pre-technical Studies", "Social Studies", "Business Studies", "Agriculture", "Life Skills Education", "Physical Education", "Creative Arts and Sports", "Religious Education"],
        "Grade 10": ["English", "Kiswahili", "Mathematics", "Community Service Learning", "Physical Education", "Biology", "Chemistry", "Physics", "Geography", "History and Citizenship", "Business Studies", "Agriculture", "Computer Studies", "Music and Dance", "Visual Arts", "Theatre and Film"],
        "Grade 11": ["English", "Kiswahili", "Mathematics", "Community Service Learning", "Physical Education", "Biology", "Chemistry", "Physics", "Geography", "History and Citizenship", "Business Studies", "Agriculture", "Computer Studies", "Music and Dance", "Visual Arts", "Theatre and Film"],
        "Grade 12": ["English", "Kiswahili", "Mathematics", "Community Service Learning", "Physical Education", "Biology", "Chemistry", "Physics", "Geography", "History and Citizenship", "Business Studies", "Agriculture", "Computer Studies", "Music and Dance", "Visual Arts", "Theatre and Film"],
    }.items()
}
CURRICULUM_BANDS = (
    ("pre-primary", "PP1-PP2", ("PP1", "PP2")),
    ("lower-primary", "Grade 1-3", ("Grade 1", "Grade 2", "Grade 3")),
    ("upper-primary", "Grade 4-6", ("Grade 4", "Grade 5", "Grade 6")),
    ("junior-school", "Grade 7-9", ("Grade 7", "Grade 8", "Grade 9")),
    ("senior-school", "Grade 10-12", ("Grade 10", "Grade 11", "Grade 12")),
)
BAND_BY_GRADE = {
    grade: band for band, _label, grades in CURRICULUM_BANDS for grade in grades
}
ALLOWED_ADMIN_TASKS = {"classes", "resources", "learners"}


def valid_curriculum_name(value: object, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value.strip()) <= max_length
        and not any(ord(character) < 32 for character in value)
    )


def load_curriculum_source() -> dict[str, object]:
    return json.loads(CURRICULUM_CONTENT_FILE.read_text(encoding="utf-8"))


def curriculum_catalog() -> dict[str, object]:
    grades = list(CBC_GRADES)
    subjects = {grade: list(items) for grade, items in CBC_SUBJECTS.items()}
    content_source = load_curriculum_source()
    band_content = content_source["content"]
    curriculum_content = {}
    for grade in grades:
        grade_subjects = band_content.get(BAND_BY_GRADE.get(grade, ""), {})
        grade_content = {}
        for subject in subjects[grade]:
            guide = grade_subjects.get(subject)
            if guide is None:
                continue
            grade_content[subject] = {
                "overview": guide["overview"],
                "unitCount": len(guide["units"]),
                "grade_focus": content_source["grade_focus"][grade],
            }
        curriculum_content[grade] = grade_content
    with connect_db() as db:
        custom_grades = db.execute(
            """
            SELECT grade FROM video_classes
            UNION SELECT grade FROM resources WHERE grade IS NOT NULL
            """
        ).fetchall()
        custom_subjects = db.execute(
            """
            SELECT grade, subject FROM video_classes
            UNION SELECT grade, subject FROM resources
            """
        ).fetchall()
    for row in custom_grades:
        grade = row["grade"]
        if valid_curriculum_name(grade, 80) and grade not in grades:
            grades.append(grade)
    for row in custom_subjects:
        grade = row["grade"]
        subject = row["subject"]
        if not valid_curriculum_name(grade, 80) or not valid_curriculum_name(subject, 100):
            continue
        if grade not in grades:
            grades.append(grade)
        grade_subjects = subjects.setdefault(grade, [])
        if subject not in grade_subjects:
            grade_subjects.append(subject)
    return {
        "grades": grades,
        "subjects": subjects,
        "curriculumContent": {
            "scope": content_source["scope"],
            "notice": content_source["notice"],
            "byGrade": curriculum_content,
        },
    }


def curriculum_subject_guide(grade: str, subject: str) -> dict[str, object]:
    if not valid_curriculum_name(grade, 80) or not valid_curriculum_name(subject, 100):
        raise ApiError(400, "Provide a valid grade and subject.")
    band = BAND_BY_GRADE.get(grade.strip())
    if band is None:
        raise ApiError(404, "That grade does not have a learning guide yet.")
    content_source = load_curriculum_source()
    guide = content_source["content"].get(band, {}).get(subject.strip())
    if guide is None:
        raise ApiError(404, "That learning guide is not available yet.")
    return {
        "grade": grade.strip(),
        "subject": subject.strip(),
        "band": band,
        "overview": guide["overview"],
        "gradeFocus": content_source["grade_focus"].get(grade.strip(), ""),
        "units": guide["units"],
    }


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE, timeout=10, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'student')),
                status TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Suspended')),
                xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
                lessons_completed INTEGER NOT NULL DEFAULT 0 CHECK (lessons_completed >= 0),
                learning_minutes INTEGER NOT NULL DEFAULT 0 CHECK (learning_minutes >= 0),
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_admin_only
                ON users(role) WHERE role = 'admin';
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
            CREATE TABLE IF NOT EXISTS learner_presence (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                activity TEXT NOT NULL CHECK (activity IN ('lesson_complete', 'challenge_correct', 'topic_quiz')),
                xp INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS achievements (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                icon TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS achievement_awards (
                id TEXT PRIMARY KEY,
                achievement_id TEXT NOT NULL REFERENCES achievements(id) ON DELETE RESTRICT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                awarded_by TEXT NOT NULL REFERENCES users(id),
                awarded_at TEXT NOT NULL,
                revoked_by TEXT REFERENCES users(id),
                revoked_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_achievement_award
                ON achievement_awards(achievement_id, user_id) WHERE revoked_at IS NULL;
            CREATE TABLE IF NOT EXISTS certificates (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                source_type TEXT NOT NULL CHECK (source_type IN ('quiz', 'exam')),
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                score INTEGER CHECK (score IS NULL OR score >= 0),
                total INTEGER CHECK (total IS NULL OR total > 0),
                requirement TEXT NOT NULL,
                issued_by TEXT REFERENCES users(id) ON DELETE SET NULL,
                signature_name TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                UNIQUE(user_id, source_type, source_id),
                CHECK ((score IS NULL AND total IS NULL) OR
                       (score IS NOT NULL AND total IS NOT NULL AND score <= total))
            );
            CREATE INDEX IF NOT EXISTS certificates_user_issued
                ON certificates(user_id, issued_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS one_daily_challenge_per_user
                ON activity_events(user_id, date(created_at))
                WHERE activity = 'challenge_correct';
            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('Notes', 'Exam')),
                size INTEGER NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                subject TEXT,
                grade TEXT,
                uploaded_by TEXT REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS video_classes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                subject TEXT NOT NULL,
                grade TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                video_url TEXT NOT NULL,
                embed_url TEXT NOT NULL,
                scheduled_at TEXT,
                created_by TEXT NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS moderator_invites (
                id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL COLLATE NOCASE,
                scopes_json TEXT NOT NULL,
                invited_by TEXT NOT NULL REFERENCES users(id),
                expires_at TEXT NOT NULL,
                accepted_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delegated_admins (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                invite_id TEXT NOT NULL REFERENCES moderator_invites(id),
                scopes_json TEXT NOT NULL,
                granted_by TEXT NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delegated_class_access (
                user_id TEXT NOT NULL REFERENCES delegated_admins(user_id) ON DELETE CASCADE,
                class_id TEXT NOT NULL REFERENCES video_classes(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, class_id)
            );
            CREATE TABLE IF NOT EXISTS learner_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                group_type TEXT NOT NULL CHECK (group_type IN ('study', 'class')),
                grade TEXT,
                description TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                CHECK ((group_type = 'study' AND grade IS NULL) OR
                       (group_type = 'class' AND grade IS NOT NULL))
            );
            CREATE TABLE IF NOT EXISTS learner_group_members (
                group_id TEXT NOT NULL REFERENCES learner_groups(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                added_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        for column in ("school", "grade"):
            if column not in user_columns:
                db.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT")
        resource_columns = {row["name"] for row in db.execute("PRAGMA table_info(resources)")}
        for column, definition in (
            ("subject", "TEXT"),
            ("grade", "TEXT"),
            ("uploaded_by", "TEXT REFERENCES users(id) ON DELETE SET NULL"),
        ):
            if column not in resource_columns:
                db.execute(f"ALTER TABLE resources ADD COLUMN {column} {definition}")
        activity_table = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'activity_events'"
        ).fetchone()
        if activity_table and "topic_quiz" not in activity_table["sql"]:
            db.executescript(
                """
                ALTER TABLE activity_events RENAME TO activity_events_legacy;
                CREATE TABLE activity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    activity TEXT NOT NULL CHECK (activity IN ('lesson_complete', 'challenge_correct', 'topic_quiz')),
                    xp INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                INSERT INTO activity_events (id, user_id, activity, xp, created_at)
                    SELECT id, user_id, activity, xp, created_at FROM activity_events_legacy;
                DROP TABLE activity_events_legacy;
                CREATE UNIQUE INDEX IF NOT EXISTS one_daily_challenge_per_user
                    ON activity_events(user_id, date(created_at))
                    WHERE activity = 'challenge_correct';
                """
            )
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS topic_quiz_completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                topic_id TEXT NOT NULL,
                score INTEGER NOT NULL CHECK (score >= 0),
                total INTEGER NOT NULL CHECK (total > 0),
                xp INTEGER NOT NULL CHECK (xp >= 0),
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_topic_quiz_per_user
                ON topic_quiz_completions(user_id, topic_id);
            """
        )
        defaults = {
            "challenge": True,
            "registrations": True,
            "leaderboard": True,
        }
        for key, value in defaults.items():
            db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now(),))


def local_network_addresses() -> list[str]:
    addresses = set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            parsed = ipaddress.ip_address(address)
            if parsed.is_private and not parsed.is_loopback and not parsed.is_link_local:
                addresses.add(address)
    except socket.gaierror:
        logging.warning("Could not detect local network addresses automatically.")
    return sorted(addresses)


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return salt.hex(), password_hash.hex()


def valid_email(email: str) -> bool:
    return len(email) <= 254 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is not None


def video_embed_url(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 500:
        raise ApiError(400, "Enter a valid YouTube or Vimeo video link.")
    try:
        parsed = urlsplit(value.strip())
    except ValueError as error:
        raise ApiError(400, "Enter a valid YouTube or Vimeo video link.") from error
    try:
        port = parsed.port
    except ValueError as error:
        raise ApiError(400, "Video links must use HTTPS and a supported video host.") from error
    if parsed.scheme != "https" or parsed.username or parsed.password or port:
        raise ApiError(400, "Video links must use HTTPS and a supported video host.")
    host = (parsed.hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        video_id = ""
        if parsed.path == "/watch":
            from urllib.parse import parse_qs

            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:embed|shorts|live)/([A-Za-z0-9_-]{11})", parsed.path)
            if match:
                video_id = match.group(1)
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return value.strip(), f"https://www.youtube-nocookie.com/embed/{video_id}"
    elif host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/")
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return value.strip(), f"https://www.youtube-nocookie.com/embed/{video_id}"
    elif host in {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}:
        match = re.fullmatch(r"/(?:video/)?([0-9]{1,12})/?", parsed.path)
        if match:
            return value.strip(), f"https://player.vimeo.com/video/{match.group(1)}"
    raise ApiError(400, "Use a valid YouTube or Vimeo video link.")


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(char for char in name if char.isprintable() and char not in "\r\n")
    name = name.strip(" .")
    return name[:180] or "resource"


def public_user(user: sqlite3.Row, role: str | None = None) -> dict[str, object]:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": role or user["role"],
        "school": user["school"] if "school" in user.keys() else None,
        "grade": user["grade"] if "grade" in user.keys() else None,
        "status": user["status"],
        "xp": user["xp"],
        "lessonsCompleted": user["lessons_completed"],
        "learningMinutes": user["learning_minutes"],
        "createdAt": user["created_at"],
    }


def quiz_completions(user_id: str) -> dict[str, dict[str, int]]:
    with connect_db() as db:
        rows = db.execute(
            "SELECT topic_id, score, total FROM topic_quiz_completions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {row["topic_id"]: {"score": row["score"], "total": row["total"]} for row in rows}


def record_topic_quiz(user: sqlite3.Row, payload: dict[str, object]) -> dict[str, object]:
    topic_id = payload.get("topicId")
    score = payload.get("score")
    total = payload.get("total")
    if not isinstance(topic_id, str) or not topic_id.strip() or len(topic_id.strip()) > 200:
        raise ApiError(400, "Provide a valid topic for this quiz.")
    if isinstance(total, bool) or not isinstance(total, int) or not 1 <= total <= 20:
        raise ApiError(400, "Provide a valid quiz size.")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= total:
        raise ApiError(400, "Provide a valid quiz score.")
    topic_id = topic_id.strip()
    with connect_db() as db:
        existing = db.execute(
            "SELECT score, total FROM topic_quiz_completions WHERE user_id = ? AND topic_id = ?",
            (user["id"], topic_id),
        ).fetchone()
        if existing:
            updated = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            return {
                "user": public_user(updated),
                "awardedXp": 0,
                "score": existing["score"],
                "total": existing["total"],
                "alreadyCompleted": True,
            }
        points = 10 * score + (10 if score == total else 0)
        db.execute(
            """
            INSERT INTO topic_quiz_completions (user_id, topic_id, score, total, xp, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user["id"], topic_id, score, total, points, utc_now()),
        )
        db.execute(
            "INSERT INTO activity_events (user_id, activity, xp, created_at) VALUES (?, 'topic_quiz', ?, ?)",
            (user["id"], points, utc_now()),
        )
        db.execute(
            "UPDATE users SET xp = xp + ?, learning_minutes = learning_minutes + ? WHERE id = ?",
            (points, score, user["id"]),
        )
        certificate = None
        if score * 100 >= 95 * total:
            admin = db.execute(
                "SELECT id, name FROM users WHERE role = 'admin' AND status = 'Active'"
            ).fetchone()
            if admin:
                certificate_id = str(uuid.uuid4())
                title = f"{topic_id.replace('::', ' · ')} quiz"
                db.execute(
                    """
                    INSERT INTO certificates
                    (id, user_id, source_type, source_id, title, score, total, requirement,
                     issued_by, signature_name, issued_at)
                    VALUES (?, ?, 'quiz', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        certificate_id, user["id"], topic_id, title, score, total,
                        "At least 95% required for a passing certificate.",
                        admin["id"], admin["name"], utc_now(),
                    ),
                )
                certificate = {"id": certificate_id, "title": title}
        updated = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {
        "user": public_user(updated),
        "awardedXp": points,
        "score": score,
        "total": total,
        "alreadyCompleted": False,
        "certificate": certificate,
    }


class LearnloopHandler(BaseHTTPRequestHandler):
    server_version = "Learnloop"

    def log_message(self, format_string: str, *args: object) -> None:
        logging.info("%s %s", self.address_string(), format_string % args)

    def end_headers(self) -> None:
        for keyword, value in SECURITY_HEADERS:
            self.send_header(keyword, value)
        for keyword, value in self.cors_headers():
            self.send_header(keyword, value)
        super().end_headers()

    def cors_headers(self) -> list[tuple[str, str]]:
        origin = self.headers.get("Origin", "")
        if not urlsplit(self.path).path.startswith("/api/") or origin not in CORS_ALLOWED_ORIGINS:
            return []
        return [
            ("Access-Control-Allow-Origin", origin),
            ("Access-Control-Allow-Credentials", "true"),
            ("Vary", "Origin"),
        ]

    def send_json(self, status: int, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self, limit: int = MAX_REQUEST_BYTES) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ApiError(400, "Invalid request length.") from error
        if length < 0 or length > limit:
            raise ApiError(413, "Request is too large.")
        return self.rfile.read(length)

    def read_json(self) -> dict[str, object]:
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ApiError(415, "Send JSON with Content-Type: application/json.")
        try:
            data = json.loads(self.read_body().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(400, "Invalid JSON request.") from error
        if not isinstance(data, dict):
            raise ApiError(400, "Expected a JSON object.")
        return data

    def require_same_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if (
                parsed.netloc.lower() != self.headers.get("Host", "").lower()
                and origin not in CORS_ALLOWED_ORIGINS
            ):
                raise ApiError(403, "Cross-origin requests are not allowed.")

    def current_user(self) -> sqlite3.Row | None:
        raw_cookie = self.headers.get("Cookie", "")
        cookies = http.cookies.SimpleCookie()
        try:
            cookies.load(raw_cookie)
        except http.cookies.CookieError:
            return None
        morsel = cookies.get(SESSION_COOKIE)
        if not morsel:
            return None
        token_hash = hashlib.sha256(morsel.value.encode("ascii", "ignore")).hexdigest()
        with connect_db() as db:
            return db.execute(
                """
                SELECT users.* FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (token_hash, utc_now()),
            ).fetchone()

    def require_user(self, role: str | None = None) -> sqlite3.Row:
        user = self.current_user()
        if user is None:
            raise ApiError(401, "Please sign in to continue.")
        if user["status"] != "Active":
            raise ApiError(403, "This account is suspended.")
        with connect_db() as db:
            delegated = db.execute(
                "SELECT 1 FROM delegated_admins WHERE user_id = ?", (user["id"],)
            ).fetchone()
        effective_role = "moderator" if delegated else user["role"]
        if role == "admin" and effective_role != "admin":
            raise ApiError(403, "You do not have permission to do that.")
        if role == "student" and effective_role != "student":
            raise ApiError(403, "You do not have permission to do that.")
        return user

    def effective_role(self, user: sqlite3.Row) -> str:
        with connect_db() as db:
            delegated = db.execute(
                "SELECT 1 FROM delegated_admins WHERE user_id = ?", (user["id"],)
            ).fetchone()
        return "moderator" if delegated else user["role"]

    def require_admin_permission(
        self,
        permission: str,
        *,
        grade: object = None,
        subject: object = None,
        class_id: str | None = None,
    ) -> sqlite3.Row:
        user = self.require_user()
        if user["role"] == "admin":
            return user
        if self.effective_role(user) != "moderator":
            raise ApiError(403, "You do not have permission to do that.")
        with connect_db() as db:
            row = db.execute(
                "SELECT scopes_json FROM delegated_admins WHERE user_id = ?", (user["id"],)
            ).fetchone()
            if row is None:
                raise ApiError(403, "You do not have permission to do that.")
            scopes = json.loads(row["scopes_json"])
            if permission not in scopes["permissions"]:
                raise ApiError(403, "You do not have permission to do that.")
            if grade is not None and (
                not isinstance(grade, str) or grade not in scopes["grades"]
            ):
                raise ApiError(403, "This task is outside your assigned grade scope.")
            if subject is not None and grade is not None:
                if (
                    not valid_curriculum_name(grade, 80)
                    or not valid_curriculum_name(subject, 100)
                ):
                    raise ApiError(400, "Enter a valid grade and subject.")
                if scopes.get("subjects") and subject not in scopes["subjects"]:
                    raise ApiError(403, "This subject is outside your assigned scope.")
            if class_id is not None:
                assigned = db.execute(
                    "SELECT 1 FROM delegated_class_access WHERE user_id = ? AND class_id = ?",
                    (user["id"], class_id),
                ).fetchone()
                created = db.execute(
                    "SELECT 1 FROM video_classes WHERE id = ? AND created_by = ?",
                    (class_id, user["id"]),
                ).fetchone()
                if not assigned and not created:
                    raise ApiError(403, "This video class is not assigned to you.")
        return user

    def set_session(self, user_id: str) -> None:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
        with connect_db() as db:
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (token_hash, user_id, expires, now.isoformat(timespec="seconds")),
            )
        cookie = http.cookies.SimpleCookie()
        cookie[SESSION_COOKIE] = token
        cookie[SESSION_COOKIE]["path"] = "/"
        cookie[SESSION_COOKIE]["httponly"] = True
        cross_origin = self.headers.get("Origin", "") in CORS_ALLOWED_ORIGINS
        cookie[SESSION_COOKIE]["samesite"] = "None" if cross_origin else "Strict"
        cookie[SESSION_COOKIE]["max-age"] = str(SESSION_DAYS * 24 * 60 * 60)
        if COOKIE_SECURE or cross_origin:
            cookie[SESSION_COOKIE]["secure"] = True
        self.send_header("Set-Cookie", cookie[SESSION_COOKIE].OutputString())

    def clear_session(self) -> None:
        raw_cookie = self.headers.get("Cookie", "")
        cookies = http.cookies.SimpleCookie()
        try:
            cookies.load(raw_cookie)
        except http.cookies.CookieError:
            cookies = http.cookies.SimpleCookie()
        morsel = cookies.get(SESSION_COOKIE)
        if morsel:
            token_hash = hashlib.sha256(morsel.value.encode("ascii", "ignore")).hexdigest()
            with connect_db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        cookie = http.cookies.SimpleCookie()
        cookie[SESSION_COOKIE] = ""
        cookie[SESSION_COOKIE]["path"] = "/"
        cookie[SESSION_COOKIE]["httponly"] = True
        cross_origin = self.headers.get("Origin", "") in CORS_ALLOWED_ORIGINS
        cookie[SESSION_COOKIE]["samesite"] = "None" if cross_origin else "Strict"
        cookie[SESSION_COOKIE]["max-age"] = "0"
        if COOKIE_SECURE or cross_origin:
            cookie[SESSION_COOKIE]["secure"] = True
        self.send_header("Set-Cookie", cookie[SESSION_COOKIE].OutputString())

    def create_account(
        self,
        email: object,
        name: object,
        password: object,
        role: str,
        school: str | None = None,
        grade: str | None = None,
    ) -> sqlite3.Row:
        if not isinstance(email, str) or not valid_email(email.strip()):
            raise ApiError(400, "Enter a valid email address.")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
            raise ApiError(400, "Enter a name between 1 and 60 characters.")
        if not isinstance(password, str) or len(password) < 10 or len(password) > 128:
            raise ApiError(400, "Password must be between 10 and 128 characters.")
        salt, password_hash = hash_password(password)
        user_id = str(uuid.uuid4())
        try:
            with connect_db() as db:
                db.execute(
                    """
                    INSERT INTO users
                    (id, email, name, password_salt, password_hash, role, created_at, school, grade)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        email.strip().lower(),
                        name.strip(),
                        salt,
                        password_hash,
                        role,
                        utc_now(),
                        school,
                        grade,
                    ),
                )
                user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                if user is None:
                    raise ApiError(500, "Account could not be created.")
                return user
        except sqlite3.IntegrityError as error:
            if role == "admin":
                raise ApiError(409, "The admin account has already been set up.") from error
            raise ApiError(409, "An account with that email already exists.") from error

    def check_login_limit(self) -> None:
        key = self.client_address[0]
        now = time.time()
        with LOGIN_ATTEMPTS_LOCK:
            attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(key, []) if now - stamp < 600]
            LOGIN_ATTEMPTS[key] = attempts
            if len(attempts) >= 10:
                raise ApiError(429, "Too many sign-in attempts. Try again in 10 minutes.")

    def record_failed_login(self) -> None:
        with LOGIN_ATTEMPTS_LOCK:
            LOGIN_ATTEMPTS.setdefault(self.client_address[0], []).append(time.time())

    def clear_failed_logins(self) -> None:
        with LOGIN_ATTEMPTS_LOCK:
            LOGIN_ATTEMPTS.pop(self.client_address[0], None)

    def valid_invite(self, token: object) -> sqlite3.Row:
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise ApiError(400, "The invitation link is invalid.")
        token_hash = hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()
        with connect_db() as db:
            invite = db.execute(
                "SELECT * FROM moderator_invites WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        if invite is None:
            raise ApiError(404, "The invitation link is invalid or has expired.")
        if invite["accepted_at"]:
            raise ApiError(409, "This invitation has already been used.")
        if invite["expires_at"] <= utc_now():
            raise ApiError(410, "This invitation has expired. Ask the administrator for a new one.")
        return invite

    def apply_invite(self, db: sqlite3.Connection, user_id: str, invite: sqlite3.Row) -> None:
        account = db.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        if account is None or account["email"].lower() != invite["email"].lower():
            raise ApiError(403, "Sign in or create an account using the invited email address.")
        if db.execute(
            "SELECT 1 FROM delegated_admins WHERE user_id = ?", (user_id,)
        ).fetchone():
            raise ApiError(409, "This account already has delegated administrator access.")
        scopes = json.loads(invite["scopes_json"])
        db.execute(
            """
            INSERT INTO delegated_admins (user_id, invite_id, scopes_json, granted_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, invite["id"], invite["scopes_json"], invite["invited_by"], utc_now()),
        )
        for class_id in scopes.get("session_ids", []):
            db.execute(
                "INSERT INTO delegated_class_access (user_id, class_id) VALUES (?, ?)",
                (user_id, class_id),
            )
        updated = db.execute(
            "UPDATE moderator_invites SET accepted_at = ? WHERE id = ? AND accepted_at IS NULL",
            (utc_now(), invite["id"]),
        )
        if updated.rowcount != 1:
            raise ApiError(409, "This invitation has already been used.")

    def handle_api(self, method: str, path: str) -> None:
        if method in {"POST", "PATCH", "DELETE"}:
            self.require_same_origin()

        if method == "GET" and path == "/api/health":
            self.send_json(200, {"ok": True})
            return

        if method == "GET" and path == "/api/curriculum":
            self.send_json(200, curriculum_catalog())
            return

        if method == "GET" and path == "/api/curriculum/subject":
            from urllib.parse import parse_qs

            query = parse_qs(urlsplit(self.path).query)
            guide = curriculum_subject_guide(
                query.get("grade", [""])[0], query.get("subject", [""])[0]
            )
            self.send_json(200, guide)
            return

        if method == "GET" and path == "/api/classes":
            user = self.require_user()
            role = self.effective_role(user)
            with connect_db() as db:
                if role == "admin":
                    rows = db.execute(
                        "SELECT * FROM video_classes ORDER BY COALESCE(scheduled_at, created_at) DESC"
                    ).fetchall()
                elif role == "moderator":
                    self.require_admin_permission("classes")
                    rows = db.execute(
                        """
                        SELECT DISTINCT video_classes.* FROM video_classes
                        LEFT JOIN delegated_class_access
                            ON delegated_class_access.class_id = video_classes.id
                            AND delegated_class_access.user_id = ?
                        WHERE video_classes.created_by = ? OR delegated_class_access.user_id = ?
                        ORDER BY COALESCE(video_classes.scheduled_at, video_classes.created_at) DESC
                        """,
                        (user["id"], user["id"], user["id"]),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT * FROM video_classes WHERE grade = ? "
                        "ORDER BY COALESCE(scheduled_at, created_at) DESC",
                        (user["grade"],),
                    ).fetchall()
            self.send_json(200, {"classes": [dict(row) for row in rows]})
            return

        if method == "POST" and path == "/api/invites/validate":
            payload = self.read_json()
            invite = self.valid_invite(payload.get("token"))
            scopes = json.loads(invite["scopes_json"])
            self.send_json(
                200,
                {
                    "email": invite["email"],
                    "expiresAt": invite["expires_at"],
                    "permissions": scopes["permissions"],
                    "grades": scopes["grades"],
                    "sessionIds": scopes.get("session_ids", []),
                },
            )
            return

        if method == "GET" and path == "/api/auth/status":
            with connect_db() as db:
                admin_exists = db.execute(
                    "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
                ).fetchone()
                registrations = db.execute(
                    "SELECT value FROM settings WHERE key = 'registrations'"
                ).fetchone()
            self.send_json(
                200,
                {
                    "adminConfigured": admin_exists is not None,
                    "setupEnabled": bool(ADMIN_SETUP_KEY) and admin_exists is None,
                    "registrationsOpen": registrations is None or json.loads(registrations["value"]) is True,
                },
            )
            return



        if method == "GET" and path == "/api/auth/me":
            user = self.current_user()
            if user is not None and user["status"] != "Active":
                user = None
            self.send_json(
                200,
                {"user": public_user(user, self.effective_role(user)) if user else None},
            )
            return

        if method == "POST" and path == "/api/auth/signup":
            payload = self.read_json()
            if payload.get("password") != payload.get("confirmPassword"):
                raise ApiError(400, "Passwords do not match.")
            school = payload.get("school")
            grade = payload.get("grade")
            if not isinstance(school, str) or not school.strip() or len(school.strip()) > 120:
                raise ApiError(400, "Enter your school name (up to 120 characters).")
            if not valid_curriculum_name(grade, 80):
                raise ApiError(400, "Enter a grade or level (up to 80 characters).")
            grade = grade.strip()
            with connect_db() as db:
                registrations = db.execute(
                    "SELECT value FROM settings WHERE key = 'registrations'"
                ).fetchone()
            if registrations and json.loads(registrations["value"]) is False:
                raise ApiError(403, "New student registrations are currently closed.")
            user = self.create_account(
                payload.get("email"), payload.get("name"), payload.get("password"), "student",
                school.strip(), grade,
            )
            self.send_response(201)
            self.set_session(user["id"])
            body = json.dumps(
                {"user": public_user(user, self.effective_role(user))}, ensure_ascii=False
            ).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if method == "POST" and path == "/api/auth/invites/signup":
            payload = self.read_json()
            if payload.get("password") != payload.get("confirmPassword"):
                raise ApiError(400, "Passwords do not match.")
            invite = self.valid_invite(payload.get("token"))
            email = payload.get("email")
            name = payload.get("name")
            password = payload.get("password")
            if not isinstance(email, str) or email.strip().lower() != invite["email"].lower():
                raise ApiError(403, "Use the email address that received this invitation.")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
                raise ApiError(400, "Enter a name between 1 and 60 characters.")
            if not isinstance(password, str) or len(password) < 10 or len(password) > 128:
                raise ApiError(400, "Password must be between 10 and 128 characters.")
            scopes = json.loads(invite["scopes_json"])
            salt, password_hash = hash_password(password)
            user_id = str(uuid.uuid4())
            try:
                with connect_db() as db:
                    db.execute(
                        """
                        INSERT INTO users
                        (id, email, name, password_salt, password_hash, role, created_at, grade)
                        VALUES (?, ?, ?, ?, ?, 'student', ?, ?)
                        """,
                        (user_id, email.strip().lower(), name.strip(), salt, password_hash,
                         utc_now(), (scopes["grades"] or [None])[0]),
                    )
                    self.apply_invite(db, user_id, invite)
                    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            except sqlite3.IntegrityError as error:
                raise ApiError(409, "An account with that email already exists.") from error
            self.send_response(201)
            self.set_session(user_id)
            body = json.dumps(
                {"user": public_user(user, "moderator")}, ensure_ascii=False
            ).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if method == "POST" and path == "/api/auth/invites/accept":
            user = self.require_user("student")
            payload = self.read_json()
            invite = self.valid_invite(payload.get("token"))
            with connect_db() as db:
                self.apply_invite(db, user["id"], invite)
                updated_user = db.execute(
                    "SELECT * FROM users WHERE id = ?", (user["id"],)
                ).fetchone()
            self.send_json(200, {"user": public_user(updated_user, "moderator")})
            return

        if method == "POST" and path == "/api/auth/admin-setup":
            if not ADMIN_SETUP_KEY:
                raise ApiError(503, "Admin setup is disabled. Configure ADMIN_SETUP_KEY on the server first.")
            payload = self.read_json()
            if payload.get("password") != payload.get("confirmPassword"):
                raise ApiError(400, "Passwords do not match.")
            provided_key = payload.get("setupKey")
            if not isinstance(provided_key, str) or not hmac.compare_digest(
                provided_key.encode("utf-8"), ADMIN_SETUP_KEY.encode("utf-8")
            ):
                raise ApiError(403, "The admin setup key is incorrect.")
            with connect_db() as db:
                admin_exists = db.execute(
                    "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
                ).fetchone()
            if admin_exists:
                raise ApiError(409, "Admin setup is complete. Sign in with the admin account.")
            user = self.create_account(
                payload.get("email"), payload.get("name"), payload.get("password"), "admin"
            )
            self.send_response(201)
            self.set_session(user["id"])
            body = json.dumps(
                {"user": public_user(user, self.effective_role(user))}, ensure_ascii=False
            ).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if method == "POST" and path == "/api/auth/login":
            self.check_login_limit()
            payload = self.read_json()
            email = payload.get("email")
            password = payload.get("password")
            if not isinstance(email, str) or not isinstance(password, str):
                raise ApiError(400, "Enter your email and password.")
            with connect_db() as db:
                user = db.execute(
                    "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
                ).fetchone()
            valid = False
            if user:
                _, calculated_hash = hash_password(password, bytes.fromhex(user["password_salt"]))
                valid = hmac.compare_digest(calculated_hash, user["password_hash"])
            else:
                hash_password(password, bytes(16))
            if not user or not valid:
                self.record_failed_login()
                raise ApiError(401, "Email or password is incorrect.")
            if user["status"] != "Active":
                raise ApiError(403, "This account is suspended. Contact the admin.")
            self.clear_failed_logins()
            self.send_response(200)
            self.set_session(user["id"])
            body = json.dumps(
                {"user": public_user(user, self.effective_role(user))}, ensure_ascii=False
            ).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if method == "POST" and path == "/api/auth/logout":
            self.send_response(200)
            self.clear_session()
            body = b'{"ok":true}'
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if method == "GET" and path == "/api/student/progress":
            user = self.require_user("student")
            self.send_json(
                200,
                {
                    "user": public_user(user, self.effective_role(user)),
                    "quizCompletions": quiz_completions(user["id"]),
                },
            )
            return

        if method == "POST" and path == "/api/student/activity":
            user = self.require_user("student")
            payload = self.read_json()
            activity = payload.get("activity")
            if activity == "topic_quiz":
                self.send_json(200, record_topic_quiz(user, payload))
                return
            reward = {"lesson_complete": (50, 1, 5), "challenge_correct": (80, 0, 5)}
            if activity not in reward:
                raise ApiError(400, "Unknown learning activity.")
            if activity == "challenge_correct":
                with connect_db() as db:
                    challenge_setting = db.execute(
                        "SELECT value FROM settings WHERE key = 'challenge'"
                    ).fetchone()
                if challenge_setting and json.loads(challenge_setting["value"]) is False:
                    raise ApiError(403, "The daily challenge is currently disabled by the admin.")
            points, lesson_count, minutes = reward[activity]
            try:
                with connect_db() as db:
                    if activity == "lesson_complete" and user["lessons_completed"] >= 1:
                        raise ApiError(409, "Your beginner lesson is already complete.")
                    if activity == "challenge_correct":
                        completed_today = db.execute(
                            """
                            SELECT 1 FROM activity_events
                            WHERE user_id = ? AND activity = 'challenge_correct'
                            AND date(created_at) = date('now') LIMIT 1
                            """,
                            (user["id"],),
                        ).fetchone()
                        if completed_today:
                            raise ApiError(409, "Today's challenge has already been completed.")
                    db.execute(
                        "INSERT INTO activity_events (user_id, activity, xp, created_at) VALUES (?, ?, ?, ?)",
                        (user["id"], activity, points, utc_now()),
                    )
                    db.execute(
                        """
                        UPDATE users SET xp = xp + ?, lessons_completed = lessons_completed + ?,
                        learning_minutes = learning_minutes + ? WHERE id = ?
                        """,
                        (points, lesson_count, minutes, user["id"]),
                    )
                    updated = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            except sqlite3.IntegrityError as error:
                raise ApiError(409, "Today's challenge has already been completed.") from error
            self.send_json(200, {"user": public_user(updated)})
            return

        if method == "PATCH" and path == "/api/student/profile":
            user = self.require_user("student")
            payload = self.read_json()
            school = payload.get("school")
            grade = payload.get("grade")
            if not isinstance(school, str) or not school.strip() or len(school.strip()) > 120:
                raise ApiError(400, "Enter your school name (up to 120 characters).")
            if not valid_curriculum_name(grade, 80):
                raise ApiError(400, "Enter a grade or level (up to 80 characters).")
            grade = grade.strip()
            with connect_db() as db:
                updated = db.execute(
                    "UPDATE users SET school = ?, grade = ? WHERE id = ? AND role = 'student'",
                    (school.strip(), grade, user["id"]),
                )
                if updated.rowcount != 1:
                    raise ApiError(404, "Student account not found.")
                profile = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            self.send_json(200, {"user": public_user(profile)})
            return

        if method == "GET" and path == "/api/student/settings":
            self.require_user("student")
            with connect_db() as db:
                settings = {
                    row["key"]: json.loads(row["value"])
                    for row in db.execute("SELECT key, value FROM settings")
                }
            self.send_json(200, {"settings": settings})
            return

        if method == "GET" and path == "/api/admin/overview":
            self.require_user("admin")
            with connect_db() as db:
                students = db.execute(
                    """
                    SELECT users.id, users.name, users.email, users.status, users.xp,
                    users.lessons_completed, users.created_at, users.school, users.grade
                    FROM users WHERE users.role = 'student'
                    AND NOT EXISTS (SELECT 1 FROM delegated_admins WHERE user_id = users.id)
                    ORDER BY users.created_at DESC
                    """
                ).fetchall()
                resource_count = db.execute("SELECT COUNT(*) AS count FROM resources").fetchone()["count"]
                activity_count = db.execute("SELECT COUNT(*) AS count FROM activity_events").fetchone()["count"]
                learning_minutes = db.execute(
                    """
                    SELECT COALESCE(SUM(learning_minutes), 0) AS total FROM users
                    WHERE role = 'student' AND NOT EXISTS
                    (SELECT 1 FROM delegated_admins WHERE user_id = users.id)
                    """
                ).fetchone()["total"]
                settings = {
                    row["key"]: json.loads(row["value"])
                    for row in db.execute("SELECT key, value FROM settings")
                }
                recent_activity = db.execute(
                    """
                    SELECT activity_events.activity, activity_events.xp, activity_events.created_at,
                    users.name AS student_name FROM activity_events
                    JOIN users ON users.id = activity_events.user_id
                    ORDER BY activity_events.id DESC LIMIT 10
                    """
                ).fetchall()
            self.send_json(
                200,
                {
                    "studentCount": len(students),
                    "resourceCount": resource_count,
                    "activityCount": activity_count,
                    "learningMinutes": learning_minutes,
                    "students": [dict(row) for row in students],
                    "settings": settings,
                    "recentActivity": [dict(row) for row in recent_activity],
                },
            )
            return

        if method == "GET" and path == "/api/admin/students":
            user = self.require_user()
            if user["role"] == "admin":
                with connect_db() as db:
                    students = db.execute(
                        """
                        SELECT users.id, users.name, users.email, users.status, users.xp,
                        users.lessons_completed, users.created_at, users.school, users.grade
                        FROM users WHERE users.role = 'student'
                        AND NOT EXISTS (SELECT 1 FROM delegated_admins WHERE user_id = users.id)
                        ORDER BY users.created_at DESC
                        """
                    ).fetchall()
            else:
                self.require_admin_permission("learners")
                with connect_db() as db:
                    scopes_row = db.execute(
                        "SELECT scopes_json FROM delegated_admins WHERE user_id = ?",
                        (user["id"],),
                    ).fetchone()
                    grades = json.loads(scopes_row["scopes_json"])["grades"]
                    if not grades:
                        students = []
                    else:
                        placeholders = ",".join("?" for _ in grades)
                        students = db.execute(
                            f"""
                            SELECT users.id, users.name, users.email, users.status, users.xp,
                            users.lessons_completed, users.created_at, users.school, users.grade
                            FROM users WHERE users.role = 'student' AND users.grade IN ({placeholders})
                            AND NOT EXISTS (SELECT 1 FROM delegated_admins WHERE user_id = users.id)
                            ORDER BY created_at DESC
                            """,
                            grades,
                        ).fetchall()
            self.send_json(200, {"students": [dict(row) for row in students]})
            return

        if method == "POST" and path == "/api/admin/students":
            user = self.require_user("admin")
            payload = self.read_json()
            password = payload.get("password")
            if password != payload.get("confirmPassword"):
                raise ApiError(400, "Passwords do not match.")
            school = payload.get("school", "")
            grade = payload.get("grade")
            if not isinstance(school, str) or len(school.strip()) > 120:
                raise ApiError(400, "School name must be 120 characters or fewer.")
            if not valid_curriculum_name(grade, 80):
                raise ApiError(400, "Enter a grade or level (up to 80 characters).")
            student = self.create_account(
                payload.get("email"),
                payload.get("name"),
                password,
                "student",
                school.strip() or None,
                grade.strip(),
            )
            self.send_json(201, {"user": public_user(student)})
            return

        if method == "DELETE" and path.startswith("/api/admin/students/"):
            self.require_user("admin")
            student_id = unquote(path[len("/api/admin/students/"):].strip("/"))
            if not student_id or "/" in student_id:
                raise ApiError(404, "Student account not found.")
            with connect_db() as db:
                student = db.execute(
                    """
                    SELECT id FROM users WHERE id = ? AND role = 'student'
                    AND NOT EXISTS (SELECT 1 FROM delegated_admins WHERE user_id = users.id)
                    """,
                    (student_id,),
                ).fetchone()
                if student is None:
                    raise ApiError(404, "Student account not found.")
                db.execute("DELETE FROM users WHERE id = ?", (student_id,))
            self.send_json(200, {"ok": True})
            return

        if method == "GET" and path == "/api/admin/groups":
            self.require_user("admin")
            with connect_db() as db:
                groups = [
                    dict(row)
                    for row in db.execute(
                        """
                        SELECT id, name, group_type, grade, description, created_at
                        FROM learner_groups ORDER BY created_at DESC, name COLLATE NOCASE
                        """
                    ).fetchall()
                ]
                members = db.execute(
                    """
                    SELECT learner_group_members.group_id, users.id, users.name, users.email,
                    users.grade, users.status
                    FROM learner_group_members
                    JOIN users ON users.id = learner_group_members.user_id
                    ORDER BY users.name COLLATE NOCASE
                    """
                ).fetchall()
            members_by_group: dict[str, list[dict[str, object]]] = {
                group["id"]: [] for group in groups
            }
            for member in members:
                if member["group_id"] in members_by_group:
                    members_by_group[member["group_id"]].append(
                        {key: member[key] for key in ("id", "name", "email", "grade", "status")}
                    )
            for group in groups:
                group["members"] = members_by_group[group["id"]]
            self.send_json(200, {"groups": groups})
            return

        if method == "POST" and path == "/api/admin/groups":
            user = self.require_user("admin")
            payload = self.read_json()
            name = payload.get("name")
            group_type = payload.get("type")
            description = payload.get("description", "")
            grade = payload.get("grade")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
                raise ApiError(400, "Group name must be between 1 and 80 characters.")
            if not isinstance(group_type, str) or group_type not in {"study", "class"}:
                raise ApiError(400, "Choose a study group or grade/class group.")
            if not isinstance(description, str) or len(description.strip()) > 300:
                raise ApiError(400, "Group description must be 300 characters or fewer.")
            if group_type == "class":
                if not valid_curriculum_name(grade, 80):
                    raise ApiError(400, "Choose a valid grade for this group.")
                grade = grade.strip()
            else:
                grade = None
            group_id = str(uuid.uuid4())
            with connect_db() as db:
                db.execute(
                    """
                    INSERT INTO learner_groups
                    (id, name, group_type, grade, description, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (group_id, name.strip(), group_type, grade, description.strip(), user["id"], utc_now()),
                )
            self.send_json(201, {"id": group_id})
            return

        if method == "POST" and path.startswith("/api/admin/groups/") and path.endswith("/members"):
            self.require_user("admin")
            group_id = unquote(path[len("/api/admin/groups/"):-len("/members")].strip("/"))
            payload = self.read_json()
            student_id = payload.get("studentId")
            if not isinstance(student_id, str) or not student_id.strip():
                raise ApiError(400, "Choose a student to add.")
            with connect_db() as db:
                group = db.execute(
                    "SELECT group_type, grade FROM learner_groups WHERE id = ?", (group_id,)
                ).fetchone()
                student = db.execute(
                    """
                    SELECT id, grade, status FROM users WHERE id = ? AND role = 'student'
                    AND NOT EXISTS (SELECT 1 FROM delegated_admins WHERE user_id = users.id)
                    """,
                    (student_id.strip(),),
                ).fetchone()
                if group is None:
                    raise ApiError(404, "Group not found.")
                if student is None or student["status"] != "Active":
                    raise ApiError(404, "Active student account not found.")
                if group["group_type"] == "class" and student["grade"] != group["grade"]:
                    raise ApiError(400, "Only students in this group's grade can be added.")
                try:
                    db.execute(
                        """
                        INSERT INTO learner_group_members (group_id, user_id, added_at)
                        VALUES (?, ?, ?)
                        """,
                        (group_id, student["id"], utc_now()),
                    )
                except sqlite3.IntegrityError as error:
                    raise ApiError(409, "This student is already in the group.") from error
            self.send_json(201, {"ok": True})
            return

        if method == "DELETE" and path.startswith("/api/admin/groups/"):
            self.require_user("admin")
            parts = path.strip("/").split("/")
            if len(parts) == 5 and parts[0:3] == ["api", "admin", "groups"] and parts[3] and parts[4] == "members":
                raise ApiError(404, "Group member not found.")
            if len(parts) == 6 and parts[0:3] == ["api", "admin", "groups"] and parts[4] == "members":
                group_id = unquote(parts[3])
                student_id = unquote(parts[5])
                with connect_db() as db:
                    deleted = db.execute(
                        "DELETE FROM learner_group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, student_id),
                    )
                    if deleted.rowcount != 1:
                        raise ApiError(404, "Group member not found.")
                self.send_json(200, {"ok": True})
                return
            if len(parts) == 4 and parts[0:3] == ["api", "admin", "groups"]:
                group_id = unquote(parts[3])
                with connect_db() as db:
                    deleted = db.execute("DELETE FROM learner_groups WHERE id = ?", (group_id,))
                    if deleted.rowcount != 1:
                        raise ApiError(404, "Group not found.")
                self.send_json(200, {"ok": True})
                return

        if method == "GET" and path == "/api/admin/access":
            user = self.require_user()
            if user["role"] == "admin":
                self.send_json(
                    200,
                    {
                        "permissions": [
                            "overview", "competitions", "resources", "settings",
                            "learners", "classes", "delegates", "achievements", "groups",
                        ],
                        "grades": curriculum_catalog()["grades"],
                        "role": "admin",
                    },
                )
                return
            if self.effective_role(user) != "moderator":
                raise ApiError(403, "You do not have permission to do that.")
            with connect_db() as db:
                scope_row = db.execute(
                    "SELECT scopes_json FROM delegated_admins WHERE user_id = ?",
                    (user["id"],),
                ).fetchone()
            scopes = json.loads(scope_row["scopes_json"])
            self.send_json(
                200,
                {
                    "permissions": scopes["permissions"],
                    "grades": scopes["grades"],
                    "sessionIds": scopes.get("session_ids", []),
                    "role": "moderator",
                },
            )
            return

        if method == "GET" and path == "/api/admin/achievements":
            self.require_user("admin")
            with connect_db() as db:
                achievements = db.execute(
                    """
                    SELECT achievements.*,
                    (SELECT COUNT(*) FROM achievement_awards
                     WHERE achievement_id = achievements.id AND revoked_at IS NULL) AS award_count
                    FROM achievements ORDER BY is_active DESC, name COLLATE NOCASE ASC
                    """
                ).fetchall()
                awards = db.execute(
                    """
                    SELECT achievement_awards.id, achievement_awards.achievement_id,
                    achievement_awards.user_id AS student_id, achievement_awards.awarded_at,
                    achievement_awards.revoked_at, achievements.name AS achievement_name,
                    achievements.icon AS achievement_icon, achievements.category,
                    users.name AS student_name, users.email AS student_email, users.grade,
                    awarded_by.name AS awarded_by_name, revoked_by.name AS revoked_by_name
                    FROM achievement_awards
                    JOIN achievements ON achievements.id = achievement_awards.achievement_id
                    JOIN users ON users.id = achievement_awards.user_id
                    JOIN users AS awarded_by ON awarded_by.id = achievement_awards.awarded_by
                    LEFT JOIN users AS revoked_by ON revoked_by.id = achievement_awards.revoked_by
                    WHERE NOT EXISTS (
                        SELECT 1 FROM delegated_admins WHERE user_id = users.id
                    )
                    ORDER BY achievement_awards.awarded_at DESC, achievement_awards.id DESC
                    """
                ).fetchall()
                students = db.execute(
                    """
                    SELECT users.id, users.name, users.email, users.grade
                    FROM users WHERE users.role = 'student' AND users.status = 'Active'
                    AND NOT EXISTS (
                        SELECT 1 FROM delegated_admins WHERE user_id = users.id
                    )
                    ORDER BY users.name COLLATE NOCASE ASC
                    """
                ).fetchall()
            self.send_json(
                200,
                {
                    "achievements": [dict(row) for row in achievements],
                    "awards": [dict(row) for row in awards],
                    "students": [dict(row) for row in students],
                },
            )
            return

        if method == "POST" and path == "/api/admin/achievements":
            user = self.require_user("admin")
            payload = self.read_json()
            name = payload.get("name")
            description = payload.get("description")
            category = payload.get("category")
            icon = payload.get("icon")
            if (
                not valid_curriculum_name(name, 60)
                or not valid_curriculum_name(description, 200)
                or not valid_curriculum_name(category, 40)
                or not isinstance(icon, str)
                or not icon.strip()
                or len(icon.strip()) > 8
                or any(ord(character) < 32 for character in icon)
            ):
                raise ApiError(400, "Enter a valid achievement name, description, category, and icon.")
            achievement_id = str(uuid.uuid4())
            now = utc_now()
            with connect_db() as db:
                db.execute(
                    """
                    INSERT INTO achievements
                    (id, name, description, category, icon, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        achievement_id, name.strip(), description.strip(), category.strip(),
                        icon.strip(), now, now,
                    ),
                )
            self.send_json(201, {"id": achievement_id})
            return

        if method == "PATCH" and path.startswith("/api/admin/achievements/"):
            self.require_user("admin")
            achievement_id = unquote(path[len("/api/admin/achievements/"):].strip("/"))
            if not achievement_id or "/" in achievement_id:
                raise ApiError(404, "Achievement not found.")
            payload = self.read_json()
            name = payload.get("name")
            description = payload.get("description")
            category = payload.get("category")
            icon = payload.get("icon")
            is_active = payload.get("isActive")
            if (
                not valid_curriculum_name(name, 60)
                or not valid_curriculum_name(description, 200)
                or not valid_curriculum_name(category, 40)
                or not isinstance(icon, str)
                or not icon.strip()
                or len(icon.strip()) > 8
                or any(ord(character) < 32 for character in icon)
                or (is_active is not None and not isinstance(is_active, bool))
            ):
                raise ApiError(400, "Enter valid achievement details.")
            with connect_db() as db:
                result = db.execute(
                    """
                    UPDATE achievements SET name = ?, description = ?, category = ?, icon = ?,
                    is_active = COALESCE(?, is_active), updated_at = ? WHERE id = ?
                    """,
                    (
                        name.strip(), description.strip(), category.strip(), icon.strip(),
                        int(is_active) if is_active is not None else None, utc_now(), achievement_id,
                    ),
                )
                if result.rowcount != 1:
                    raise ApiError(404, "Achievement not found.")
            self.send_json(200, {"ok": True})
            return

        if method == "POST" and path.startswith("/api/admin/achievements/") and path.endswith("/awards"):
            user = self.require_user("admin")
            achievement_id = unquote(
                path[len("/api/admin/achievements/"):-len("/awards")].strip("/")
            )
            payload = self.read_json()
            student_id = payload.get("studentId")
            if not achievement_id or "/" in achievement_id:
                raise ApiError(404, "Achievement not found.")
            if not isinstance(student_id, str) or not student_id:
                raise ApiError(400, "Choose a learner to receive this achievement.")
            award_id = str(uuid.uuid4())
            try:
                with connect_db() as db:
                    achievement = db.execute(
                        "SELECT is_active FROM achievements WHERE id = ?", (achievement_id,)
                    ).fetchone()
                    if achievement is None:
                        raise ApiError(404, "Achievement not found.")
                    if not achievement["is_active"]:
                        raise ApiError(409, "Archived achievements cannot be awarded.")
                    student = db.execute(
                        """
                        SELECT id FROM users WHERE id = ? AND role = 'student' AND status = 'Active'
                        AND NOT EXISTS (
                            SELECT 1 FROM delegated_admins WHERE user_id = users.id
                        )
                        """,
                        (student_id,),
                    ).fetchone()
                    if student is None:
                        raise ApiError(404, "Active learner not found.")
                    db.execute(
                        """
                        INSERT INTO achievement_awards
                        (id, achievement_id, user_id, awarded_by, awarded_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (award_id, achievement_id, student_id, user["id"], utc_now()),
                    )
            except sqlite3.IntegrityError as error:
                raise ApiError(409, "This learner already has this achievement.") from error
            self.send_json(201, {"id": award_id})
            return

        if method == "DELETE" and path.startswith("/api/admin/achievement-awards/"):
            user = self.require_user("admin")
            award_id = unquote(path[len("/api/admin/achievement-awards/"):].strip("/"))
            if not award_id or "/" in award_id:
                raise ApiError(404, "Active achievement award not found.")
            with connect_db() as db:
                result = db.execute(
                    """
                    UPDATE achievement_awards SET revoked_by = ?, revoked_at = ?
                    WHERE id = ? AND revoked_at IS NULL
                    """,
                    (user["id"], utc_now(), award_id),
                )
                if result.rowcount != 1:
                    raise ApiError(404, "Active achievement award not found.")
            self.send_json(200, {"ok": True})
            return

        if method == "POST" and path == "/api/admin/invites":
            self.require_user("admin")
            payload = self.read_json()
            email = payload.get("email")
            permissions = payload.get("permissions")
            grades = payload.get("grades")
            session_ids = payload.get("sessionIds", [])
            if not isinstance(email, str) or not valid_email(email.strip()):
                raise ApiError(400, "Enter a valid helper email address.")
            if (
                not isinstance(permissions, list)
                or not permissions
                or any(
                    not isinstance(item, str) or item not in ALLOWED_ADMIN_TASKS
                    for item in permissions
                )
            ):
                raise ApiError(400, "Select at least one valid helper task.")
            if (
                not isinstance(grades, list)
                or not grades
                or any(not valid_curriculum_name(grade, 80) for grade in grades)
            ):
                raise ApiError(400, "Enter one or more valid grade scopes.")
            grades = [grade.strip() for grade in grades]
            if not isinstance(session_ids, list) or any(
                not isinstance(class_id, str) for class_id in session_ids
            ):
                raise ApiError(400, "Choose valid video classes for the session scope.")
            if session_ids and "classes" not in permissions:
                raise ApiError(400, "Video-class sessions require the video class task.")
            if len(set(permissions)) != len(permissions) or len(set(grades)) != len(grades):
                raise ApiError(400, "Duplicate task or grade scopes are not allowed.")
            if len(set(session_ids)) != len(session_ids):
                raise ApiError(400, "Duplicate video-class selections are not allowed.")
            token = secrets.token_urlsafe(36)
            invite_id = str(uuid.uuid4())
            created = datetime.now(timezone.utc)
            expires = (created + timedelta(days=7)).isoformat(timespec="seconds")
            scopes = {
                "permissions": permissions,
                "grades": grades,
                "subjects": [],
                "session_ids": session_ids,
            }
            try:
                with connect_db() as db:
                    if session_ids:
                        placeholders = ",".join("?" for _ in session_ids)
                        sessions = db.execute(
                            f"SELECT id, grade FROM video_classes WHERE id IN ({placeholders})",
                            session_ids,
                        ).fetchall()
                        if len(sessions) != len(set(session_ids)) or any(
                            row["grade"] not in grades for row in sessions
                        ):
                            raise ApiError(400, "Each selected video class must match an allowed grade.")
                    pending = db.execute(
                        """
                        SELECT 1 FROM moderator_invites WHERE email = ? COLLATE NOCASE
                        AND accepted_at IS NULL AND expires_at > ? LIMIT 1
                        """,
                        (email.strip(), utc_now()),
                    ).fetchone()
                    if pending:
                        raise ApiError(409, "An active invitation already exists for that email.")
                    existing = db.execute(
                        "SELECT id, role FROM users WHERE email = ? COLLATE NOCASE",
                        (email.strip(),),
                    ).fetchone()
                    if existing and existing["role"] != "student":
                        raise ApiError(409, "That email cannot be granted delegated access.")
                    if existing and db.execute(
                        "SELECT 1 FROM delegated_admins WHERE user_id = ?", (existing["id"],)
                    ).fetchone():
                        raise ApiError(409, "That account already has delegated access.")
                    db.execute(
                        """
                        INSERT INTO moderator_invites
                        (id, token_hash, email, scopes_json, invited_by, expires_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            invite_id,
                            hashlib.sha256(token.encode("ascii")).hexdigest(),
                            email.strip().lower(),
                            json.dumps(scopes),
                            self.require_user("admin")["id"],
                            expires,
                            utc_now(),
                        ),
                    )
            except sqlite3.IntegrityError as error:
                raise ApiError(409, "An active invitation already exists for that email.") from error
            self.send_json(
                201,
                {"invite": {"token": token, "email": email.strip().lower(), "expiresAt": expires}},
            )
            return

        if method == "GET" and path == "/api/admin/delegates":
            self.require_user("admin")
            with connect_db() as db:
                rows = db.execute(
                    """
                    SELECT users.id, users.name, users.email, delegated_admins.scopes_json,
                    delegated_admins.created_at
                    FROM delegated_admins JOIN users ON users.id = delegated_admins.user_id
                    ORDER BY delegated_admins.created_at DESC
                    """
                ).fetchall()
                pending = db.execute(
                    """
                    SELECT email, scopes_json, expires_at, created_at FROM moderator_invites
                    WHERE accepted_at IS NULL AND expires_at > ? ORDER BY created_at DESC
                    """,
                    (utc_now(),),
                ).fetchall()
            self.send_json(
                200,
                {
                    "delegates": [
                        {
                            "id": row["id"],
                            "name": row["name"],
                            "email": row["email"],
                            "scopes": json.loads(row["scopes_json"]),
                            "createdAt": row["created_at"],
                        }
                        for row in rows
                    ],
                    "pendingInvites": [
                        {
                            "email": row["email"],
                            "scopes": json.loads(row["scopes_json"]),
                            "expiresAt": row["expires_at"],
                        }
                        for row in pending
                    ],
                },
            )
            return

        if method == "DELETE" and path.startswith("/api/admin/delegates/"):
            self.require_user("admin")
            user_id = unquote(path.rsplit("/", 1)[-1])
            with connect_db() as db:
                result = db.execute(
                    "DELETE FROM delegated_admins WHERE user_id = ?", (user_id,)
                )
                if result.rowcount == 0:
                    raise ApiError(404, "Delegated admin not found.")
                db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self.send_json(200, {"ok": True})
            return

        if method == "POST" and path == "/api/admin/classes":
            payload = self.read_json()
            grade = payload.get("grade")
            subject = payload.get("subject")
            user = self.require_admin_permission("classes", grade=grade, subject=subject)
            title = payload.get("title")
            description = payload.get("description", "")
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
                raise ApiError(400, "Enter a class title between 1 and 120 characters.")
            if not isinstance(description, str) or len(description) > 500:
                raise ApiError(400, "Class descriptions must be 500 characters or fewer.")
            if (
                not valid_curriculum_name(grade, 80)
                or not valid_curriculum_name(subject, 100)
            ):
                raise ApiError(400, "Enter a grade and subject.")
            grade = grade.strip()
            subject = subject.strip()
            video_url, embed_url = video_embed_url(payload.get("videoUrl"))
            scheduled_at = payload.get("scheduledAt") or None
            if scheduled_at:
                if not isinstance(scheduled_at, str) or len(scheduled_at) > 40:
                    raise ApiError(400, "Enter a valid class schedule time.")
                try:
                    scheduled_value = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                except ValueError as error:
                    raise ApiError(400, "Enter a valid class schedule time.") from error
                if scheduled_value.tzinfo is None:
                    scheduled_value = scheduled_value.replace(tzinfo=timezone.utc)
                scheduled_at = scheduled_value.astimezone(timezone.utc).isoformat(timespec="seconds")
            class_id = str(uuid.uuid4())
            with connect_db() as db:
                db.execute(
                    """
                    INSERT INTO video_classes
                    (id, title, subject, grade, description, video_url, embed_url,
                     scheduled_at, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (class_id, title.strip(), subject, grade, description.strip(), video_url,
                     embed_url, scheduled_at, user["id"], utc_now()),
                )
            self.send_json(
                201,
                {
                    "class": {
                        "id": class_id,
                        "title": title.strip(),
                        "subject": subject,
                        "grade": grade,
                        "description": description.strip(),
                        "videoUrl": video_url,
                        "embedUrl": embed_url,
                        "scheduledAt": scheduled_at,
                    }
                },
            )
            return

        if path.startswith("/api/admin/classes/"):
            class_id = unquote(path[len("/api/admin/classes/") :].strip("/"))
            if method == "DELETE":
                user = self.require_admin_permission("classes", class_id=class_id)
                with connect_db() as db:
                    class_row = db.execute(
                        "SELECT grade, subject FROM video_classes WHERE id = ?", (class_id,)
                    ).fetchone()
                    if class_row is None:
                        raise ApiError(404, "Video class not found.")
                    self.require_admin_permission(
                        "classes", grade=class_row["grade"], subject=class_row["subject"],
                        class_id=class_id,
                    )
                    db.execute("DELETE FROM video_classes WHERE id = ?", (class_id,))
                self.send_json(200, {"ok": True})
                return
            if method == "PATCH":
                payload = self.read_json()
                grade = payload.get("grade")
                subject = payload.get("subject")
                title = payload.get("title")
                description = payload.get("description", "")
                if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
                    raise ApiError(400, "Enter a class title between 1 and 120 characters.")
                if not isinstance(description, str) or len(description) > 500:
                    raise ApiError(400, "Class descriptions must be 500 characters or fewer.")
                if (
                    not valid_curriculum_name(grade, 80)
                    or not valid_curriculum_name(subject, 100)
                ):
                    raise ApiError(400, "Enter a grade and subject.")
                grade = grade.strip()
                subject = subject.strip()
                user = self.require_admin_permission(
                    "classes", grade=grade, subject=subject, class_id=class_id
                )
                video_url, embed_url = video_embed_url(payload.get("videoUrl"))
                scheduled_at = payload.get("scheduledAt") or None
                if scheduled_at:
                    if not isinstance(scheduled_at, str) or len(scheduled_at) > 40:
                        raise ApiError(400, "Enter a valid class schedule time.")
                    try:
                        scheduled_value = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                    except ValueError as error:
                        raise ApiError(400, "Enter a valid class schedule time.") from error
                    if scheduled_value.tzinfo is None:
                        scheduled_value = scheduled_value.replace(tzinfo=timezone.utc)
                    scheduled_at = scheduled_value.astimezone(timezone.utc).isoformat(timespec="seconds")
                if scheduled_at:
                    if not isinstance(scheduled_at, str) or len(scheduled_at) > 40:
                        raise ApiError(400, "Enter a valid class schedule time.")
                    try:
                        scheduled_value = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                    except ValueError as error:
                        raise ApiError(400, "Enter a valid class schedule time.") from error
                    if scheduled_value.tzinfo is None:
                        scheduled_value = scheduled_value.replace(tzinfo=timezone.utc)
                    scheduled_at = scheduled_value.astimezone(timezone.utc).isoformat(timespec="seconds")
                with connect_db() as db:
                    existing = db.execute(
                        "SELECT grade, subject FROM video_classes WHERE id = ?", (class_id,)
                    ).fetchone()
                    if existing is None:
                        raise ApiError(404, "Video class not found.")
                    self.require_admin_permission(
                        "classes", grade=existing["grade"], subject=existing["subject"],
                        class_id=class_id,
                    )
                    db.execute(
                        """
                        UPDATE video_classes SET title = ?, subject = ?, grade = ?, description = ?,
                        video_url = ?, embed_url = ?, scheduled_at = ? WHERE id = ?
                        """,
                        (title.strip(), subject, grade, description.strip(), video_url, embed_url,
                         scheduled_at, class_id),
                    )
                self.send_json(200, {"ok": True, "updatedBy": user["id"]})
                return

        if method == "GET" and path in {"/api/admin/leaderboard", "/api/leaderboard"}:
            if path == "/api/admin/leaderboard":
                self.require_user("admin")
            else:
                self.require_user("student")
                with connect_db() as db:
                    leaderboard_setting = db.execute(
                        "SELECT value FROM settings WHERE key = 'leaderboard'"
                    ).fetchone()
                if leaderboard_setting and json.loads(leaderboard_setting["value"]) is False:
                    raise ApiError(403, "The leaderboard is currently hidden by the admin.")
            query = urlsplit(self.path).query
            from urllib.parse import parse_qs

            period = parse_qs(query).get("period", ["week"])[0]
            if period not in {"week", "month", "all"}:
                raise ApiError(400, "Invalid leaderboard period.")
            if period == "week":
                date_filter = "date(activity_events.created_at) >= date('now', '-7 days')"
            elif period == "month":
                date_filter = "date(activity_events.created_at) >= date('now', '-30 days')"
            else:
                date_filter = "1 = 1"
            admin_columns = """
                    users.grade,
                    (SELECT COUNT(*) FROM achievement_awards
                     WHERE achievement_awards.user_id = users.id
                     AND achievement_awards.revoked_at IS NULL) AS achievement_count,
                    """ if path == "/api/admin/leaderboard" else ""
            with connect_db() as db:
                rows = db.execute(
                    f"""
                    SELECT users.id, users.name, users.xp, users.lessons_completed,
                    {admin_columns}
                    COALESCE(SUM(activity_events.xp), 0) AS points
                    FROM users LEFT JOIN activity_events ON activity_events.user_id = users.id
                    AND {date_filter}
                    WHERE users.role = 'student' AND users.status = 'Active'
                    AND NOT EXISTS (SELECT 1 FROM delegated_admins WHERE user_id = users.id)
                    GROUP BY users.id ORDER BY points DESC, users.name COLLATE NOCASE ASC
                    """
                ).fetchall()
            self.send_json(200, {"period": period, "standings": [dict(row) for row in rows]})
            return

        if method == "GET" and path == "/api/admin/settings":
            self.require_user("admin")
            with connect_db() as db:
                settings = {
                    row["key"]: json.loads(row["value"])
                    for row in db.execute("SELECT key, value FROM settings")
                }
            self.send_json(200, {"settings": settings})
            return

        if method == "PATCH" and path == "/api/admin/settings":
            self.require_user("admin")
            payload = self.read_json()
            if not payload or any(
                key not in {"challenge", "registrations", "leaderboard"}
                or not isinstance(value, bool)
                for key, value in payload.items()
            ):
                raise ApiError(400, "Only boolean platform settings can be changed.")
            with connect_db() as db:
                for key, value in payload.items():
                    db.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, json.dumps(value)),
                    )
            self.send_json(200, {"settings": payload})
            return

        if method == "GET" and path == "/api/resources":
            user = self.require_user()
            role = self.effective_role(user)
            with connect_db() as db:
                if role == "admin":
                    rows = db.execute(
                        "SELECT id, name, kind, size, created_at, subject, grade FROM resources "
                        "ORDER BY created_at DESC"
                    ).fetchall()
                elif role == "student":
                    rows = db.execute(
                        """
                        SELECT id, name, kind, size, created_at, subject, grade FROM resources
                        WHERE grade IS NULL OR grade = ? ORDER BY created_at DESC
                        """,
                        (user["grade"],),
                    ).fetchall()
                else:
                    scope_row = db.execute(
                        "SELECT scopes_json FROM delegated_admins WHERE user_id = ?",
                        (user["id"],),
                    ).fetchone()
                    grades = json.loads(scope_row["scopes_json"])["grades"]
                    if grades:
                        placeholders = ",".join("?" for _ in grades)
                        rows = db.execute(
                            f"""
                            SELECT id, name, kind, size, created_at, subject, grade FROM resources
                            WHERE grade IS NULL OR grade IN ({placeholders})
                            ORDER BY created_at DESC
                            """,
                            grades,
                        ).fetchall()
                    else:
                        rows = []
            self.send_json(200, {"resources": [dict(row) for row in rows]})
            return

        if method == "POST" and path == "/api/admin/resources":
            user = self.require_user()
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ApiError(415, "Upload files with multipart/form-data.")
            body = self.read_body(MAX_REQUEST_BYTES)
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
                + body
            )
            if not message.is_multipart():
                raise ApiError(400, "Invalid file upload.")
            uploaded_file = None
            kind = None
            grade = None
            subject = None
            for part in message.iter_parts():
                disposition = part.get_content_disposition()
                field_name = part.get_param("name", header="content-disposition")
                if field_name == "file" and disposition == "form-data":
                    uploaded_file = part
                elif field_name == "kind":
                    kind = part.get_content().strip()
                elif field_name == "grade":
                    grade = part.get_content().strip()
                elif field_name == "subject":
                    subject = part.get_content().strip()
            if uploaded_file is None:
                raise ApiError(400, "Choose a file to upload.")
            if kind not in {"Notes", "Exam"}:
                raise ApiError(400, "Choose Notes or Exam as the resource type.")
            if (
                not valid_curriculum_name(grade, 80)
                or not valid_curriculum_name(subject, 100)
            ):
                raise ApiError(400, "Enter a grade and subject.")
            grade = grade.strip()
            subject = subject.strip()
            self.require_admin_permission("resources", grade=grade, subject=subject)
            original_name = safe_filename(uploaded_file.get_filename() or "")
            extension = Path(original_name).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise ApiError(400, "Supported files are PDF, DOC, DOCX, PPT, PPTX, and TXT.")
            file_bytes = uploaded_file.get_payload(decode=True) or b""
            if not file_bytes:
                raise ApiError(400, "The selected file is empty.")
            if len(file_bytes) > MAX_FILE_BYTES:
                raise ApiError(413, "Each file must be 20 MB or smaller.")
            resource_id = str(uuid.uuid4())
            stored_name = f"{resource_id}{extension}"
            destination = UPLOAD_DIR / stored_name
            temporary = UPLOAD_DIR / f".{stored_name}.tmp"
            try:
                temporary.write_bytes(file_bytes)
                temporary.replace(destination)
                with connect_db() as db:
                    db.execute(
                        """
                        INSERT INTO resources
                        (id, name, kind, size, stored_name, created_at, subject, grade, uploaded_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (resource_id, original_name, kind, len(file_bytes), stored_name, utc_now(),
                         subject, grade, user["id"]),
                    )
            except OSError:
                temporary.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                raise
            self.send_json(
                201,
                {
                    "resource": {
                        "id": resource_id,
                        "name": original_name,
                        "kind": kind,
                        "size": len(file_bytes),
                        "created_at": utc_now(),
                        "subject": subject,
                        "grade": grade,
                    }
                },
            )
            return

        if method == "DELETE" and path.startswith("/api/admin/resources/"):
            user = self.require_user()
            resource_id = unquote(path.rsplit("/", 1)[-1])
            with connect_db() as db:
                resource = db.execute(
                    "SELECT stored_name, grade, subject FROM resources WHERE id = ?",
                    (resource_id,),
                ).fetchone()
                if resource is None:
                    raise ApiError(404, "Resource not found.")
                if user["role"] != "admin" and (
                    not resource["grade"] or not resource["subject"]
                ):
                    raise ApiError(403, "This resource is outside your assigned grade scope.")
                self.require_admin_permission(
                    "resources", grade=resource["grade"], subject=resource["subject"]
                )
                db.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
            (UPLOAD_DIR / resource["stored_name"]).unlink(missing_ok=True)
            self.send_json(200, {"ok": True})
            return

        if method == "POST" and path.startswith("/api/admin/students/") and path.endswith("/status"):
            user = self.require_user()
            student_id = unquote(path[len("/api/admin/students/") : -len("/status")].strip("/"))
            payload = self.read_json()
            status = payload.get("status")
            if status not in {"Active", "Suspended"}:
                raise ApiError(400, "Choose Active or Suspended.")
            with connect_db() as db:
                student = db.execute(
                    "SELECT grade FROM users WHERE id = ? AND role = 'student'", (student_id,)
                ).fetchone()
                if student is None or db.execute(
                    "SELECT 1 FROM delegated_admins WHERE user_id = ?", (student_id,)
                ).fetchone():
                    raise ApiError(404, "Student account not found.")
                self.require_admin_permission("learners", grade=student["grade"])
                result = db.execute(
                    "UPDATE users SET status = ? WHERE id = ? AND role = 'student'",
                    (status, student_id),
                )
                if result.rowcount == 0:
                    raise ApiError(404, "Student account not found.")
                if status == "Suspended":
                    db.execute("DELETE FROM sessions WHERE user_id = ?", (student_id,))
            self.send_json(200, {"status": status})
            return

        if method == "GET" and path.startswith("/api/resources/") and path.endswith("/download"):
            user = self.require_user()
            role = self.effective_role(user)
            resource_id = unquote(path[len("/api/resources/") : -len("/download")].strip("/"))
            with connect_db() as db:
                resource = db.execute(
                    "SELECT name, stored_name, grade FROM resources WHERE id = ?", (resource_id,)
                ).fetchone()
            if resource is None:
                raise ApiError(404, "Resource not found.")
            if role == "student" and resource["grade"] not in {None, user["grade"]}:
                raise ApiError(404, "Resource not found.")
            if role == "moderator" and resource["grade"] is not None:
                with connect_db() as db:
                    scope_row = db.execute(
                        "SELECT scopes_json FROM delegated_admins WHERE user_id = ?",
                        (user["id"],),
                    ).fetchone()
                if resource["grade"] not in json.loads(scope_row["scopes_json"])["grades"]:
                    raise ApiError(404, "Resource not found.")
            file_path = UPLOAD_DIR / resource["stored_name"]
            if not file_path.is_file():
                raise ApiError(404, "The resource file is missing from server storage.")
            file_bytes = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"resource\"; filename*=UTF-8''{quote(resource['name'])}",
            )
            self.send_header("Content-Length", str(len(file_bytes)))
            self.send_header("Cache-Control", "private, no-store")
            self.end_headers()
            self.wfile.write(file_bytes)
            return

        raise ApiError(404, "API endpoint not found.")

    def handle_preflight(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin not in CORS_ALLOWED_ORIGINS:
            raise ApiError(403, "Cross-origin requests are not allowed.")
        method = self.headers.get("Access-Control-Request-Method", "").upper()
        if method not in {"GET", "POST", "PATCH", "DELETE"}:
            raise ApiError(405, "This cross-origin request method is not allowed.")
        headers = {
            header.strip().lower()
            for header in self.headers.get("Access-Control-Request-Headers", "").split(",")
            if header.strip()
        }
        if not headers.issubset({"content-type"}):
            raise ApiError(403, "These cross-origin request headers are not allowed.")
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/", "/admin", "/admin/"} else path.lstrip("/")
        if relative not in {"index.html", "app.js", "config.js", "styles.css"}:
            raise ApiError(404, "Page not found.")
        file_path = ROOT / relative
        if not file_path.is_file():
            raise ApiError(404, "Page not found.")
        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, method: str) -> None:
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/"):
                self.handle_api(method, path)
            elif method == "GET":
                self.serve_static(path)
            else:
                raise ApiError(405, "Method not allowed.")
        except ApiError as error:
            self.send_json(error.status, {"error": error.message})
        except BrokenPipeError:
            logging.info("Client closed connection while receiving %s", path)
        except Exception:
            logging.exception("Request failed: %s %s", method, path)
            if not self.wfile.closed:
                self.send_json(500, {"error": "The server could not complete this request."})

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PATCH(self) -> None:
        self.dispatch("PATCH")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")

    def do_OPTIONS(self) -> None:
        path = urlsplit(self.path).path
        try:
            if not path.startswith("/api/"):
                raise ApiError(404, "Page not found.")
            self.handle_preflight()
        except ApiError as error:
            self.send_json(error.status, {"error": error.message})


def main() -> None:
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), LearnloopHandler)
    logging.info("Learnloop running at http://%s:%s", HOST, PORT)
    with connect_db() as db:
        admin_configured = db.execute(
            "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone() is not None
    if HOST in {"0.0.0.0", "::"}:
        for address in local_network_addresses():
            logging.info("On the same Wi-Fi/LAN, open http://%s:%s", address, PORT)
        if not local_network_addresses():
            logging.warning("No local network address was detected; check this device's network settings.")
    if not ADMIN_SETUP_KEY and not admin_configured:
        logging.warning("Set ADMIN_SETUP_KEY before starting to enable one-time admin setup.")
    if HOST not in {"127.0.0.1", "localhost"}:
        logging.warning("Server is exposed beyond localhost. Use HTTPS and set COOKIE_SECURE=1.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down Learnloop.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
