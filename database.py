import sqlite3
import hashlib
import secrets

DB_NAME = "timeweaver.db"


# ---------- DATABASE CONNECTION ----------
def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------- PASSWORD HASHING ----------
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000
    ).hex()

    return f"{salt}${password_hash}"


def verify_password(password, stored_hash):
    try:
        salt, expected_hash = stored_hash.split("$", 1)
        actual_hash = hash_password(password, salt).split("$", 1)[1]
        return secrets.compare_digest(actual_hash, expected_hash)
    except (ValueError, AttributeError):
        return False


# ---------- DATABASE INITIALISATION ----------
def init_db():
    conn = get_connection()

    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL
                    CHECK(role IN ('admin', 'teacher', 'student')),
                teacher_id TEXT,
                student_group TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS teachers (
                teacher_id TEXT PRIMARY KEY,
                teacher_name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS student_groups (
                group_id TEXT PRIMARY KEY,
                branch TEXT NOT NULL,
                strength INTEGER NOT NULL CHECK(strength > 0)
            );

            CREATE TABLE IF NOT EXISTS rooms (
                room_id TEXT PRIMARY KEY,
                room_name TEXT NOT NULL,
                capacity INTEGER NOT NULL CHECK(capacity > 0)
            );

            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                day TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                teacher_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                room_id TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK(status IN ('draft', 'published')),
                FOREIGN KEY(teacher_id)
                    REFERENCES teachers(teacher_id),
                FOREIGN KEY(group_id)
                    REFERENCES student_groups(group_id),
                FOREIGN KEY(room_id)
                    REFERENCES rooms(room_id)
            );
        """)

        conn.commit()

    finally:
        conn.close()


# ---------- USER MANAGEMENT ----------
def create_user(
    username,
    password,
    role,
    teacher_id=None,
    student_group=None
):
    username = username.strip()

    if not username or not password:
        return False, "Username and password are required."

    if role not in ("admin", "teacher", "student"):
        return False, "Invalid role."

    if role == "teacher" and not teacher_id:
        return False, "Teacher ID is required."

    if role == "student" and not student_group:
        return False, "Student group is required."

    conn = get_connection()

    try:
        if role == "teacher":
            exists = conn.execute(
                "SELECT 1 FROM teachers WHERE teacher_id = ?",
                (teacher_id,)
            ).fetchone()

            if not exists:
                return False, "Teacher record does not exist."

        if role == "student":
            exists = conn.execute(
                "SELECT 1 FROM student_groups WHERE group_id = ?",
                (student_group,)
            ).fetchone()

            if not exists:
                return False, "Student group does not exist."

        conn.execute(
            """
            INSERT INTO users
                (username, password_hash, role, teacher_id, student_group)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                username,
                hash_password(password),
                role,
                teacher_id,
                student_group
            )
        )

        conn.commit()
        return True, "User created successfully."

    except sqlite3.IntegrityError:
        return False, "Username already exists or account data is invalid."

    finally:
        conn.close()


# ---------- LOGIN ----------
def authenticate_user(username, password):
    conn = get_connection()

    try:
        user = conn.execute(
            """
            SELECT id, username, password_hash, role,
                   teacher_id, student_group
            FROM users
            WHERE username = ?
            """,
            (username.strip(),)
        ).fetchone()

        if user and verify_password(password, user["password_hash"]):
            return {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
                "teacher_id": user["teacher_id"],
                "student_group": user["student_group"]
            }

        return None

    finally:
        conn.close()


# ---------- TEACHER AND GROUP SETUP ----------
def add_teacher(teacher_id, teacher_name):
    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO teachers (teacher_id, teacher_name)
            VALUES (?, ?)
            """,
            (teacher_id.strip(), teacher_name.strip())
        )
        conn.commit()
        return True

    except sqlite3.IntegrityError:
        return False

    finally:
        conn.close()


def add_student_group(group_id, branch, strength):
    if strength <= 0:
        return False

    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO student_groups (group_id, branch, strength)
            VALUES (?, ?, ?)
            """,
            (group_id.strip(), branch.strip(), strength)
        )
        conn.commit()
        return True

    except sqlite3.IntegrityError:
        return False

    finally:
        conn.close()


# ---------- DEMO DATA ----------
def seed_demo_data():
    conn = get_connection()

    try:
        conn.execute(
            "INSERT OR IGNORE INTO teachers VALUES (?, ?)",
            ("T001", "Demo Teacher One")
        )

        conn.execute(
            "INSERT OR IGNORE INTO teachers VALUES (?, ?)",
            ("T002", "Demo Teacher Two")
        )

        conn.execute(
            """
            INSERT OR IGNORE INTO student_groups
                (group_id, branch, strength)
            VALUES (?, ?, ?)
            """,
            ("CSE-A", "CSE", 50)
        )

        conn.execute(
            """
            INSERT OR IGNORE INTO student_groups
                (group_id, branch, strength)
            VALUES (?, ?, ?)
            """,
            ("CSE-B", "CSE", 25)
        )

        conn.commit()

    finally:
        conn.close()

    # Demo accounts are created only if their usernames do not exist.
    demo_accounts = [
        ("admin", "ChangeMe_Admin_2026!", "admin", None, None),
        ("teacher1", "ChangeMe_Teacher_2026!", "teacher", "T001", None),
        ("teacher2", "ChangeMe_Teacher_2026!", "teacher", "T002", None),
        ("student1", "ChangeMe_Student_2026!", "student", None, "CSE-A"),
        ("student2", "ChangeMe_Student_2026!", "student", None, "CSE-B")
    ]

    for username, password, role, teacher_id, group_id in demo_accounts:
        conn = get_connection()

        try:
            exists = conn.execute(
                "SELECT 1 FROM users WHERE username = ?",
                (username,)
            ).fetchone()

            if not exists:
                create_user(
                    username,
                    password,
                    role,
                    teacher_id=teacher_id,
                    student_group=group_id
                )

        finally:
            conn.close()


if __name__ == "__main__":
    init_db()
    seed_demo_data()
    print("TimeWeaver database initialised successfully.")