import json
import sqlite3
from collections import Counter
from datetime import datetime, time

import pandas as pd
import streamlit as st

from database import (
    init_db,
    seed_demo_data,
    get_connection,
    create_user,
    authenticate_user,
    add_teacher,
    add_student_group,
)
from scheduler import generate_timetable


# -----------------------------------------------------------------------------
# APP CONFIG + THEME
# -----------------------------------------------------------------------------
st.set_page_config(page_title="TimeWeaver", page_icon="📅", layout="wide")
st.markdown(
    """
    <style>
    .stApp { background-color: #0B1120; color: #F1F5F9; }
    [data-testid="stSidebar"] { background-color: #111B2E; }
    h1, h2, h3 { color: #67E8F9 !important; }
    div[data-testid="stMetric"] {
        background-color: #142238; padding: 16px;
        border-radius: 12px; border: 1px solid #263B55;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
PRIORITY_LABELS = {3: "High", 2: "Medium", 1: "Low"}
PRIORITY_VALUES = {"High": 3, "Medium": 2, "Low": 1}


# -----------------------------------------------------------------------------
# DB HELPERS
# -----------------------------------------------------------------------------
def db_query(sql, params=()):
    conn = get_connection()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def db_one(sql, params=()):
    conn = get_connection()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def db_execute(sql, params=()):
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def ensure_app_schema():
    """Add application tables without removing the user's existing database."""
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS required_sessions (
                session_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                teacher_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                duration INTEGER NOT NULL CHECK(duration > 0),
                strength INTEGER NOT NULL CHECK(strength > 0),
                priority INTEGER NOT NULL DEFAULT 2 CHECK(priority BETWEEN 1 AND 3),
                FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id),
                FOREIGN KEY(group_id) REFERENCES student_groups(group_id)
            );

            CREATE TABLE IF NOT EXISTS timetable_breaks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                days_json TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def bootstrap_demo_data():
    """Seed records that let the existing scheduler be demonstrated immediately."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO teachers (teacher_id, teacher_name) VALUES (?, ?)",
            ("T003", "Demo Teacher Three"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO rooms (room_id, room_name, capacity) VALUES (?, ?, ?)",
            ("R101", "Room 101", 60),
        )
        conn.execute(
            "INSERT OR IGNORE INTO rooms (room_id, room_name, capacity) VALUES (?, ?, ?)",
            ("R102", "Room 102", 30),
        )
        sample_count = conn.execute(
            "SELECT COUNT(*) AS n FROM required_sessions"
        ).fetchone()["n"]
        if sample_count == 0:
            sample_sessions = [
                ("S1", "Python Programming", "T001", "CSE-A", 60, 50, 3),
                ("S2", "Mathematics", "T002", "CSE-A", 60, 50, 3),
                ("S3", "English", "T001", "CSE-B", 60, 25, 1),
                ("S4", "Computer Fundamentals", "T003", "CSE-B", 60, 25, 3),
            ]
            conn.executemany(
                """INSERT INTO required_sessions
                   (session_id, subject, teacher_id, group_id, duration, strength, priority)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                sample_sessions,
            )
        breaks_count = conn.execute(
            "SELECT COUNT(*) AS n FROM timetable_breaks"
        ).fetchone()["n"]
        if breaks_count == 0:
            conn.execute(
                """INSERT INTO timetable_breaks (name, start_time, end_time, days_json)
                   VALUES (?, ?, ?, ?)""",
                (
                    "Tea Break",
                    "10:30",
                    "10:45",
                    json.dumps(DAYS),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # Ensure a demo account exists for T003 as well.
    existing = db_one("SELECT id FROM users WHERE username = ?", ("teacher3",))
    if not existing:
        create_user(
            "teacher3",
            "ChangeMe_Teacher_2026!",
            "teacher",
            teacher_id="T003",
        )


def initialise_application():
    init_db()
    ensure_app_schema()
    seed_demo_data()
    bootstrap_demo_data()


initialise_application()


# -----------------------------------------------------------------------------
# READING AND DISPLAY HELPERS
# -----------------------------------------------------------------------------
def get_teachers():
    return db_query("SELECT teacher_id, teacher_name FROM teachers ORDER BY teacher_id")


def get_groups():
    return db_query(
        "SELECT group_id, branch, strength FROM student_groups ORDER BY group_id"
    )


def get_rooms():
    return db_query(
        "SELECT room_id, room_name, capacity FROM rooms ORDER BY room_id"
    )


def get_sessions():
    return db_query(
        """SELECT session_id, subject, teacher_id, group_id, duration, strength, priority
           FROM required_sessions ORDER BY session_id"""
    )


def get_breaks():
    rows = db_query(
        "SELECT id, name, start_time, end_time, days_json FROM timetable_breaks ORDER BY id"
    )
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["days"] = json.loads(item.pop("days_json"))
        except (TypeError, json.JSONDecodeError):
            item["days"] = []
        result.append(item)
    return result


def show_breaks_for_days(days_to_show):
    """Display saved breaks that apply to the currently viewed day or days."""
    matching = []
    for item in get_breaks():
        applicable = [day for day in days_to_show if day in item["days"]]
        if applicable:
            matching.append({
                "Break": item["name"],
                "Start": item["start_time"],
                "End": item["end_time"],
                "Days": ", ".join(applicable),
            })
    st.subheader("☕ Applicable breaks")
    if matching:
        st.dataframe(pd.DataFrame(matching), use_container_width=True, hide_index=True)
    else:
        st.caption("No configured breaks apply to this view.")


def get_schedule_version(version, status=None):
    sql = """SELECT s.id, s.version, s.session_id, s.subject, s.day,
                    s.start_time, s.end_time, s.teacher_id, s.group_id, s.room_id,
                    s.status, t.teacher_name, g.branch, r.room_name, r.capacity,
                    rs.duration, rs.strength, rs.priority
             FROM schedules s
             LEFT JOIN teachers t ON t.teacher_id = s.teacher_id
             LEFT JOIN student_groups g ON g.group_id = s.group_id
             LEFT JOIN rooms r ON r.room_id = s.room_id
             LEFT JOIN required_sessions rs ON rs.session_id = s.session_id
             WHERE s.version = ?"""
    params = [version]
    if status:
        sql += " AND s.status = ?"
        params.append(status)
    sql += " ORDER BY CASE s.day WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 " \
           "WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 ELSE 6 END, s.start_time"
    return db_query(sql, tuple(params))


def get_latest_version():
    row = db_one("SELECT MAX(version) AS version FROM schedules")
    return row["version"] if row and row["version"] is not None else None


def get_published_version():
    row = db_one("SELECT MAX(version) AS version FROM schedules WHERE status = 'published'")
    return row["version"] if row and row["version"] is not None else None


def schedule_rows_for_user(role, user):
    """Enforce scope in SQL using the authenticated user's stored role and ID."""
    if role == "teacher":
        return db_query(
            """SELECT s.*, t.teacher_name, g.branch, r.room_name, r.capacity,
                      rs.duration, rs.strength, rs.priority
               FROM schedules s
               LEFT JOIN teachers t ON t.teacher_id = s.teacher_id
               LEFT JOIN student_groups g ON g.group_id = s.group_id
               LEFT JOIN rooms r ON r.room_id = s.room_id
               LEFT JOIN required_sessions rs ON rs.session_id = s.session_id
               WHERE s.status = 'published' AND s.teacher_id = ?
               ORDER BY s.day, s.start_time""",
            (user["teacher_id"],),
        )
    if role == "student":
        return db_query(
            """SELECT s.*, t.teacher_name, g.branch, r.room_name, r.capacity,
                      rs.duration, rs.strength, rs.priority
               FROM schedules s
               LEFT JOIN teachers t ON t.teacher_id = s.teacher_id
               LEFT JOIN student_groups g ON g.group_id = s.group_id
               LEFT JOIN rooms r ON r.room_id = s.room_id
               LEFT JOIN required_sessions rs ON rs.session_id = s.session_id
               WHERE s.status = 'published' AND s.group_id = ?
               ORDER BY s.day, s.start_time""",
            (user["student_group"],),
        )
    return []


def display_schedule(rows):
    if not rows:
        return pd.DataFrame()
    data = []
    for row in rows:
        data.append(
            {
                "Day": row["day"],
                "Start": row["start_time"],
                "End": row["end_time"],
                "Subject": row["subject"],
                "Teacher": row["teacher_name"] or row["teacher_id"],
                "Group": row["group_id"],
                "Room": row["room_name"] or row["room_id"],
                "Class Strength": row["strength"],
            }
        )
    return pd.DataFrame(data)


def save_draft_schedule(assignments):
    if not assignments:
        raise ValueError("Cannot save an empty timetable.")
    conn = get_connection()
    try:
        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM schedules"
        ).fetchone()["next_version"]
        for item in assignments:
            conn.execute(
                """INSERT INTO schedules
                   (version, session_id, subject, day, start_time, end_time,
                    teacher_id, group_id, room_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')""",
                (
                    version,
                    item["id"],
                    item["subject"],
                    item["day"],
                    item["start"],
                    item["end"],
                    item["teacher"],
                    item["group"],
                    item["room"],
                ),
            )
        conn.commit()
        return version
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def normalise_saved_assignments(rows):
    normalised = []
    for row in rows:
        normalised.append(
            {
                "id": row["session_id"],
                "subject": row["subject"],
                "day": row["day"],
                "start": row["start_time"],
                "end": row["end_time"],
                "teacher": row["teacher_id"],
                "group": row["group_id"],
                "room": row["room_id"],
                "strength": row["strength"],
                "duration": row["duration"],
                "priority": row["priority"],
            }
        )
    return normalised


# -----------------------------------------------------------------------------
# INDEPENDENT TIMETABLE AUDIT
# -----------------------------------------------------------------------------
def minutes(value):
    return datetime.strptime(value, "%H:%M").hour * 60 + datetime.strptime(value, "%H:%M").minute


def audit_timetable(assignments, required_sessions, room_capacities, breaks):
    """Independent post-generation audit; it does not call scheduler.is_valid()."""
    issues = []
    assigned_ids = [str(a.get("id", "")) for a in assignments]
    required_ids = [str(s["session_id"]) for s in required_sessions]
    assigned_counts = Counter(assigned_ids)
    required_counts = Counter(required_ids)

    missing_ids = list((required_counts - assigned_counts).elements())
    duplicate_ids = [sid for sid, count in assigned_counts.items() if count > 1]
    unexpected_ids = list((assigned_counts - required_counts).elements())
    if missing_ids:
        issues.append("Missing required sessions: " + ", ".join(missing_ids))
    if duplicate_ids:
        issues.append("Duplicate session assignments: " + ", ".join(duplicate_ids))
    if unexpected_ids:
        issues.append("Unexpected sessions in timetable: " + ", ".join(unexpected_ids))

    session_by_id = {str(s["session_id"]): dict(s) for s in required_sessions}
    capacity_violations = 0
    break_conflicts = 0
    invalid_times = 0

    for item in assignments:
        sid = str(item.get("id", ""))
        required = session_by_id.get(sid)
        try:
            start = minutes(item["start"])
            end = minutes(item["end"])
            if start >= end:
                raise ValueError("start must be before end")
        except (KeyError, TypeError, ValueError):
            invalid_times += 1
            issues.append(f"{sid}: invalid start/end time")
            continue

        if required:
            if item.get("teacher") != required["teacher_id"]:
                issues.append(f"{sid}: assigned teacher differs from required teacher")
            if item.get("group") != required["group_id"]:
                issues.append(f"{sid}: assigned group differs from required group")
            if item.get("subject") != required["subject"]:
                issues.append(f"{sid}: assigned subject differs from required subject")
            if end - start != int(required["duration"]):
                issues.append(f"{sid}: scheduled duration does not match required duration")
            if item.get("strength") is None:
                issues.append(f"{sid}: class strength could not be verified")

        room = item.get("room")
        if room not in room_capacities:
            capacity_violations += 1
            issues.append(f"{sid}: room {room} is missing from room records")
        else:
            try:
                strength = int(item.get("strength"))
                if room_capacities[room] < strength:
                    capacity_violations += 1
                    issues.append(f"{sid}: room capacity is below class strength")
            except (ValueError, TypeError):
                capacity_violations += 1
                issues.append(f"{sid}: class strength could not be checked")

        for br in breaks:
            if item.get("day") in br.get("days", []):
                try:
                    bstart = minutes(br["start_time"])
                    bend = minutes(br["end_time"])
                    if start < bend and bstart < end:
                        break_conflicts += 1
                        issues.append(f"{sid}: overlaps {br['name']}")
                except (KeyError, TypeError, ValueError):
                    issues.append(f"Break {br.get('name', '(unnamed)')} has invalid times")

    teacher_conflicts = 0
    room_conflicts = 0
    group_conflicts = 0
    for i in range(len(assignments)):
        for j in range(i + 1, len(assignments)):
            first, second = assignments[i], assignments[j]
            if first.get("day") != second.get("day"):
                continue
            try:
                overlap = minutes(first["start"]) < minutes(second["end"]) and minutes(second["start"]) < minutes(first["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not overlap:
                continue
            if first.get("teacher") == second.get("teacher"):
                teacher_conflicts += 1
                issues.append(f"Teacher conflict: {first.get('id')} and {second.get('id')}")
            if first.get("room") == second.get("room"):
                room_conflicts += 1
                issues.append(f"Room conflict: {first.get('id')} and {second.get('id')}")
            if first.get("group") == second.get("group"):
                group_conflicts += 1
                issues.append(f"Student-group conflict: {first.get('id')} and {second.get('id')}")

    metrics = {
        "Required sessions": len(required_sessions),
        "Assigned sessions": len(assignments),
        "Unassigned sessions": len(missing_ids),
        "Teacher conflicts": teacher_conflicts,
        "Room conflicts": room_conflicts,
        "Group conflicts": group_conflicts,
        "Capacity violations": capacity_violations,
        "Break conflicts": break_conflicts,
        "Invalid time intervals": invalid_times,
    }
    # Repeated messages for a category are allowed because each one corresponds to a concrete violation.
    return {"ok": len(issues) == 0, "issues": issues, "metrics": metrics}


def audit_rows_from_version(version):
    rows = get_schedule_version(version)
    return normalise_saved_assignments(rows)


# -----------------------------------------------------------------------------
# LOGIN
# -----------------------------------------------------------------------------
st.title("📅 TimeWeaver")
st.caption("Smart Scheduling. Zero Clashes. Better Use of Time.")

if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

if st.session_state.auth_user is None:
    st.markdown("## 🔐 Sign in to TimeWeaver")
    st.write("Your portal is selected automatically from your registered account role.")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary", use_container_width=True)
    if submitted:
        user = authenticate_user(username, password)
        if user:
            st.session_state.auth_user = user
            st.rerun()
        else:
            st.error("Login failed. Check your username and password.")

    with st.expander("Demo accounts for local testing"):
        st.warning("These are demonstration credentials. Change them before any public deployment.")
        st.code(
            "Admin:   admin / ChangeMe_Admin_2026!\n"
            "Teacher: teacher1 / ChangeMe_Teacher_2026!\n"
            "Teacher: teacher2 / ChangeMe_Teacher_2026!\n"
            "Teacher: teacher3 / ChangeMe_Teacher_2026!\n"
            "Student: student1 / ChangeMe_Student_2026!\n"
            "Student: student2 / ChangeMe_Student_2026!",
            language="text",
        )
    st.stop()

user = st.session_state.auth_user
role = user["role"]
st.sidebar.title("TimeWeaver")
st.sidebar.caption(f"Signed in as **{user['username']}**")
st.sidebar.caption(f"Role: {role.title()}")
if st.sidebar.button("Log out", use_container_width=True):
    st.session_state.auth_user = None
    st.rerun()
st.sidebar.divider()


# -----------------------------------------------------------------------------
# ROLE-SPECIFIC PORTALS
# -----------------------------------------------------------------------------
if role == "admin":
    pages = [
        "Admin Overview",
        "Manage Sessions",
        "Manage Teachers",
        "Manage Student Groups",
        "Manage Rooms",
        "Manage Breaks",
        "Generate Timetable",
        "Draft & Publish",
        "Published Timetable",
    ]
    page = st.sidebar.radio("Admin Portal", pages)

    if page == "Admin Overview":
        st.header("👑 Admin Portal")
        st.write("Manage the college timetable from one place.")
        teachers = get_teachers()
        groups = get_groups()
        rooms = get_rooms()
        sessions = get_sessions()
        published_version = get_published_version()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Required Sessions", len(sessions))
        c2.metric("Teachers", len(teachers))
        c3.metric("Student Groups", len(groups))
        c4.metric("Classrooms", len(rooms))
        st.divider()
        if published_version is None:
            st.info("No timetable has been published yet. Generate a draft, audit it, then publish it.")
        else:
            st.success(f"Currently published timetable version: {published_version}")
        st.markdown("**Workflow:** manage data → generate draft → independent audit → publish timetable.")

    elif page == "Manage Sessions":
        st.header("📚 Manage Required Sessions")
        teachers = get_teachers()
        groups = get_groups()
        current_sessions = get_sessions()
        if not teachers or not groups:
            st.warning("Add at least one teacher and one student group before creating sessions.")
        else:
            teacher_names = {r["teacher_id"]: f"{r['teacher_id']} — {r['teacher_name']}" for r in teachers}
            group_names = {r["group_id"]: f"{r['group_id']} — {r['branch']} ({r['strength']} students)" for r in groups}
            with st.expander("➕ Add a required class session", expanded=not current_sessions):
                with st.form("add_session_form"):
                    new_id = st.text_input("Unique session ID", placeholder="S5")
                    new_subject = st.text_input("Subject name")
                    new_teacher = st.selectbox("Teacher", list(teacher_names), format_func=lambda x: teacher_names[x])
                    new_group = st.selectbox("Student group", list(group_names), format_func=lambda x: group_names[x])
                    new_duration = st.number_input("Duration (minutes)", min_value=15, max_value=240, value=60, step=15)
                    new_strength = st.number_input("Class strength", min_value=1, max_value=1000, value=30)
                    new_priority_label = st.selectbox("Subject priority", ["High", "Medium", "Low"])
                    add_session_pressed = st.form_submit_button("Add session", type="primary")
                if add_session_pressed:
                    if not new_id.strip() or not new_subject.strip():
                        st.error("Enter both a session ID and subject name.")
                    elif db_one("SELECT session_id FROM required_sessions WHERE session_id = ?", (new_id.strip(),)):
                        st.error("That session ID already exists.")
                    elif int(new_strength) > int(next(g["strength"] for g in groups if g["group_id"] == new_group)):
                        st.error("Class strength cannot exceed the selected group's registered strength.")
                    else:
                        try:
                            db_execute(
                                """INSERT INTO required_sessions
                                   (session_id, subject, teacher_id, group_id, duration, strength, priority)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (new_id.strip(), new_subject.strip(), new_teacher, new_group,
                                 int(new_duration), int(new_strength), PRIORITY_VALUES[new_priority_label]),
                            )
                            st.success("Session added and saved to SQLite.")
                            st.rerun()
                        except sqlite3.Error as exc:
                            st.error(f"Could not save session: {exc}")

        current_sessions = get_sessions()
        st.subheader("Existing sessions")
        if current_sessions:
            table = []
            for s in current_sessions:
                table.append({
                    "ID": s["session_id"], "Subject": s["subject"], "Teacher ID": s["teacher_id"],
                    "Group": s["group_id"], "Duration": s["duration"], "Strength": s["strength"],
                    "Priority": PRIORITY_LABELS.get(s["priority"], "Medium")
                })
            st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)
            session_ids = [s["session_id"] for s in current_sessions]
            selected_id = st.selectbox("Choose a session to edit or delete", session_ids, key="session_to_edit")
            selected = next(s for s in current_sessions if s["session_id"] == selected_id)
            teachers = get_teachers()
            groups = get_groups()
            teacher_ids = [t["teacher_id"] for t in teachers]
            group_ids = [g["group_id"] for g in groups]
            with st.form(f"edit_session_{selected_id}"):
                edit_subject = st.text_input("Subject", value=selected["subject"], key=f"edit_subject_{selected_id}")
                edit_teacher = st.selectbox("Teacher", teacher_ids, index=teacher_ids.index(selected["teacher_id"]), key=f"edit_teacher_{selected_id}")
                edit_group = st.selectbox("Group", group_ids, index=group_ids.index(selected["group_id"]), key=f"edit_group_{selected_id}")
                edit_duration = st.number_input("Duration (minutes)", min_value=15, max_value=240, value=int(selected["duration"]), step=15, key=f"edit_duration_{selected_id}")
                edit_strength = st.number_input("Class strength", min_value=1, max_value=1000, value=int(selected["strength"]), key=f"edit_strength_{selected_id}")
                edit_priority = st.selectbox("Priority", ["High", "Medium", "Low"], index={3: 0, 2: 1, 1: 2}.get(selected["priority"], 1), key=f"edit_priority_{selected_id}")
                save_edit = st.form_submit_button("Save session changes")
            if save_edit:
                group_strength = next(g["strength"] for g in groups if g["group_id"] == edit_group)
                if not edit_subject.strip():
                    st.error("Subject name cannot be empty.")
                elif edit_strength > group_strength:
                    st.error("Class strength cannot exceed the selected group's strength.")
                else:
                    db_execute(
                        """UPDATE required_sessions SET subject=?, teacher_id=?, group_id=?,
                           duration=?, strength=?, priority=? WHERE session_id=?""",
                        (edit_subject.strip(), edit_teacher, edit_group, int(edit_duration),
                         int(edit_strength), PRIORITY_VALUES[edit_priority], selected_id),
                    )
                    st.success("Session updated.")
                    st.rerun()
            if st.button("Delete selected session", key=f"delete_session_{selected_id}"):
                db_execute("DELETE FROM required_sessions WHERE session_id = ?", (selected_id,))
                st.warning("Session deleted. Any old draft containing it will fail the next audit.")
                st.rerun()
        else:
            st.info("No sessions have been added yet.")

    elif page == "Manage Teachers":
        st.header("👨‍🏫 Manage Teachers")
        with st.form("add_teacher_form"):
            tid = st.text_input("Teacher ID", placeholder="T004")
            tname = st.text_input("Teacher name")
            uname = st.text_input("Login username")
            upass = st.text_input("Temporary password", type="password")
            submitted = st.form_submit_button("Add teacher and login", type="primary")
        if submitted:
            if not all([tid.strip(), tname.strip(), uname.strip(), upass]):
                st.error("Complete all fields.")
            elif db_one("SELECT 1 FROM teachers WHERE teacher_id=?", (tid.strip(),)):
                st.error("That teacher ID already exists.")
            elif db_one("SELECT 1 FROM users WHERE username=?", (uname.strip(),)):
                st.error("That username already exists.")
            else:
                if add_teacher(tid.strip(), tname.strip()):
                    ok, message = create_user(uname.strip(), upass, "teacher", teacher_id=tid.strip())
                    if ok:
                        st.success("Teacher record and login account created.")
                    else:
                        st.error(f"Teacher was added, but account creation failed: {message}")
                    st.rerun()
                else:
                    st.error("Could not create the teacher record.")
        rows = get_teachers()
        st.subheader("Registered teachers")
        st.dataframe(pd.DataFrame([dict(r) for r in rows]), use_container_width=True, hide_index=True)

    elif page == "Manage Student Groups":
        st.header("🎓 Manage Student Groups")
        with st.form("add_group_form"):
            gid = st.text_input("Group ID", placeholder="CSE-C")
            branch = st.text_input("Branch", value="CSE")
            group_strength = st.number_input("Group strength", min_value=1, max_value=1000, value=30)
            st.caption("Optionally create the first student login for this group.")
            student_username = st.text_input("Student username (optional)")
            student_password = st.text_input("Student temporary password (optional)", type="password")
            submitted = st.form_submit_button("Add student group", type="primary")
        if submitted:
            if not gid.strip() or not branch.strip():
                st.error("Group ID and branch are required.")
            elif bool(student_username.strip()) != bool(student_password):
                st.error("Enter both student username and password, or leave both empty.")
            elif student_username.strip() and db_one("SELECT 1 FROM users WHERE username=?", (student_username.strip(),)):
                st.error("That student username already exists.")
            elif add_student_group(gid.strip(), branch.strip(), int(group_strength)):
                if student_username.strip():
                    ok, message = create_user(
                        student_username.strip(), student_password, "student", student_group=gid.strip()
                    )
                    if ok:
                        st.success("Group and student login created.")
                    else:
                        st.error(f"Group created, but student login failed: {message}")
                else:
                    st.success("Student group created. You can create a student login by adding another group-specific account later.")
                st.rerun()
            else:
                st.error("Could not create group. Check for a duplicate group ID.")
        st.subheader("Registered groups")
        st.dataframe(pd.DataFrame([dict(r) for r in get_groups()]), use_container_width=True, hide_index=True)

    elif page == "Manage Rooms":
        st.header("🏫 Manage Rooms")
        with st.form("add_room_form"):
            room_id = st.text_input("Room ID", placeholder="R103")
            room_name = st.text_input("Room name", placeholder="Computer Lab 1")
            capacity = st.number_input("Room capacity", min_value=1, max_value=2000, value=40)
            submitted = st.form_submit_button("Add room", type="primary")
        if submitted:
            if not room_id.strip() or not room_name.strip():
                st.error("Room ID and name are required.")
            elif db_one("SELECT 1 FROM rooms WHERE room_id=?", (room_id.strip(),)):
                st.error("That room ID already exists.")
            else:
                try:
                    db_execute(
                        "INSERT INTO rooms (room_id, room_name, capacity) VALUES (?, ?, ?)",
                        (room_id.strip(), room_name.strip(), int(capacity)),
                    )
                    st.success("Room added.")
                    st.rerun()
                except sqlite3.Error as exc:
                    st.error(f"Could not add room: {exc}")
        rooms = get_rooms()
        st.subheader("Current rooms")
        st.dataframe(pd.DataFrame([dict(r) for r in rooms]), use_container_width=True, hide_index=True)
        if rooms:
            chosen_room = st.selectbox("Room to edit or remove", [r["room_id"] for r in rooms])
            chosen = next(r for r in rooms if r["room_id"] == chosen_room)
            with st.form(f"edit_room_{chosen_room}"):
                edited_name = st.text_input("Room name", value=chosen["room_name"], key=f"room_name_{chosen_room}")
                edited_capacity = st.number_input("Capacity", min_value=1, max_value=2000, value=int(chosen["capacity"]), key=f"room_capacity_{chosen_room}")
                save_room = st.form_submit_button("Save room changes")
            if save_room:
                db_execute(
                    "UPDATE rooms SET room_name=?, capacity=? WHERE room_id=?",
                    (edited_name.strip(), int(edited_capacity), chosen_room),
                )
                st.success("Room updated.")
                st.rerun()
            if st.button("Delete selected room", key=f"delete_room_{chosen_room}"):
                try:
                    db_execute("DELETE FROM rooms WHERE room_id=?", (chosen_room,))
                    st.success("Room deleted.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("This room is referenced by a saved timetable and cannot be deleted.")

    elif page == "Manage Breaks":
        st.header("☕ Manage Breaks")
        with st.form("add_break_form"):
            name = st.text_input("Break name", placeholder="Lunch Break")
            start = st.time_input("Start time", value=time(10, 30))
            end = st.time_input("End time", value=time(10, 45))
            selected_break_days = st.multiselect("Applicable days", DAYS, default=DAYS)
            submitted = st.form_submit_button("Add break", type="primary")
        if submitted:
            if not name.strip():
                st.error("Enter a break name.")
            elif start >= end:
                st.error("Break end time must be later than start time.")
            elif not selected_break_days:
                st.error("Select at least one day.")
            else:
                db_execute(
                    "INSERT INTO timetable_breaks (name, start_time, end_time, days_json) VALUES (?, ?, ?, ?)",
                    (name.strip(), start.strftime("%H:%M"), end.strftime("%H:%M"), json.dumps(selected_break_days)),
                )
                st.success("Break added and saved.")
                st.rerun()
        breaks = get_breaks()
        st.subheader("Configured breaks")
        if breaks:
            st.dataframe(pd.DataFrame([
                {"ID": b["id"], "Name": b["name"], "Start": b["start_time"], "End": b["end_time"], "Days": ", ".join(b["days"])}
                for b in breaks
            ]), use_container_width=True, hide_index=True)
            selected_break = st.selectbox("Break to remove", [b["id"] for b in breaks], format_func=lambda x: next(b["name"] for b in breaks if b["id"] == x))
            if st.button("Delete selected break"):
                db_execute("DELETE FROM timetable_breaks WHERE id=?", (selected_break,))
                st.success("Break deleted.")
                st.rerun()
        else:
            st.info("No breaks configured.")

    elif page == "Generate Timetable":
        st.header("🧠 Generate Timetable")
        st.write("Create a new draft from the sessions, rooms and breaks saved in SQLite.")
        sessions_rows = get_sessions()
        room_rows = get_rooms()
        break_rows = get_breaks()
        if not sessions_rows:
            st.warning("Add at least one required session first.")
        elif not room_rows:
            st.warning("Add at least one classroom first.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                opening = st.time_input("College opening time", value=time(9, 0))
            with col2:
                closing = st.time_input("College closing time", value=time(16, 0))
            interval = st.selectbox("Candidate start-time interval", [30, 60], index=1, format_func=lambda x: f"{x} minutes")
            selected_days = st.multiselect("Working days", DAYS, default=DAYS)
            st.caption("High-priority subjects are preferred earlier where the current solver ordering permits; this is not an optimality guarantee.")
            if st.button("🚀 Generate timetable draft", type="primary"):
                if opening >= closing:
                    st.error("Closing time must be later than opening time.")
                elif not selected_days:
                    st.error("Select at least one working day.")
                else:
                    sessions = [
                        {
                            "id": r["session_id"], "subject": r["subject"],
                            "teacher": r["teacher_id"], "group": r["group_id"],
                            "duration": int(r["duration"]), "strength": int(r["strength"]),
                            "priority": int(r["priority"]),
                        }
                        for r in sessions_rows
                    ]
                    room_capacities = {r["room_id"]: int(r["capacity"]) for r in room_rows}
                    break_context = [
                        {"name": b["name"], "start": b["start_time"], "end": b["end_time"], "days": b["days"]}
                        for b in break_rows
                    ]
                    start_times = []
                    current = opening.hour * 60 + opening.minute
                    closing_minutes = closing.hour * 60 + closing.minute
                    while current < closing_minutes:
                        start_times.append(f"{current // 60:02d}:{current % 60:02d}")
                        current += interval
                    context = {
                        "opening": opening.strftime("%H:%M"),
                        "closing": closing.strftime("%H:%M"),
                        "rooms": room_capacities,
                        "breaks": break_context,
                    }
                    with st.spinner("Running the scheduling solver..."):
                        try:
                            assignments, status = generate_timetable(sessions, selected_days, start_times, context)
                        except Exception as exc:
                            st.error(f"The scheduler raised an error: {exc}")
                            assignments, status = [], "ERROR"
                    if status == "VALID":
                        session_by_id = {s["id"]: s for s in sessions}
                        enriched = []
                        for a in assignments:
                            item = dict(a)
                            item["duration"] = session_by_id[item["id"]]["duration"]
                            item["strength"] = session_by_id[item["id"]]["strength"]
                            item["priority"] = session_by_id[item["id"]]["priority"]
                            enriched.append(item)
                        audit = audit_timetable(enriched, sessions_rows, room_capacities, [
                            {"name": b["name"], "start_time": b["start_time"], "end_time": b["end_time"], "days": b["days"]}
                            for b in break_rows
                        ])
                        try:
                            version = save_draft_schedule(assignments)
                            st.session_state["last_draft_version"] = version
                            st.success(f"Draft version {version} saved to SQLite.")
                            st.write("**Independent audit result**")
                            mcols = st.columns(4)
                            for col, (label, value) in zip(mcols, list(audit["metrics"].items())[:4]):
                                col.metric(label, value)
                            if audit["ok"]:
                                st.success("Audit passed. Open Draft & Publish to review and publish this version.")
                            else:
                                st.error("Audit failed. This draft cannot be published.")
                                for issue in audit["issues"]:
                                    st.write(f"- {issue}")
                        except sqlite3.Error as exc:
                            st.error(f"Could not save the draft: {exc}")
                    else:
                        st.error("No complete timetable was found for these settings. Try adding rooms, widening the day, or reviewing breaks and class strengths.")

    elif page == "Draft & Publish":
        st.header("🧾 Draft Review & Publication")
        draft_versions = db_query("SELECT DISTINCT version FROM schedules WHERE status='draft' ORDER BY version DESC")
        if not draft_versions:
            st.info("There are no saved draft versions. Generate a timetable first.")
        else:
            versions = [r["version"] for r in draft_versions]
            default_version = st.session_state.get("last_draft_version", versions[0])
            index = versions.index(default_version) if default_version in versions else 0
            version = st.selectbox("Draft version", versions, index=index)
            rows = get_schedule_version(version, status="draft")
            if rows:
                st.dataframe(display_schedule(rows), use_container_width=True, hide_index=True)
                try:
                    saved_assignments = normalise_saved_assignments(rows)
                    required_rows = get_sessions()
                    room_capacities = {r["room_id"]: int(r["capacity"]) for r in get_rooms()}
                    breaks_for_audit = [
                        {"name": b["name"], "start_time": b["start_time"], "end_time": b["end_time"], "days": b["days"]}
                        for b in get_breaks()
                    ]
                    audit = audit_timetable(saved_assignments, required_rows, room_capacities, breaks_for_audit)
                except Exception as exc:
                    audit = {"ok": False, "issues": [f"Audit could not complete: {exc}"], "metrics": {}}
                st.subheader("Independent validation report")
                st.dataframe(pd.DataFrame([{"Metric": k, "Value": v} for k, v in audit["metrics"].items()]), use_container_width=True, hide_index=True)
                if audit["ok"]:
                    st.success("All currently implemented hard-constraint checks passed.")
                    if st.button("✅ Publish this timetable", type="primary"):
                        conn = get_connection()
                        try:
                            # Keep the existing published version available until this one passes the audit.
                            conn.execute("UPDATE schedules SET status='published' WHERE version=? AND status='draft'", (version,))
                            conn.commit()
                            st.success(f"Timetable version {version} is now published.")
                            st.rerun()
                        except sqlite3.Error as exc:
                            conn.rollback()
                            st.error(f"Could not publish timetable: {exc}")
                        finally:
                            conn.close()
                else:
                    st.error("Publication blocked: audit did not pass.")
                    for issue in audit["issues"]:
                        st.write(f"- {issue}")
                st.download_button(
                    "Download draft CSV",
                    data=display_schedule(rows).to_csv(index=False).encode("utf-8"),
                    file_name=f"timeweaver_draft_v{version}.csv",
                    mime="text/csv",
                )
            else:
                st.info("This version has no draft rows. It may already have been published.")

    elif page == "Published Timetable":
        st.header("📅 Published Timetable")
        version = get_published_version()
        if version is None:
            st.info("No timetable has been published yet.")
        else:
            rows = get_schedule_version(version, status="published")
            st.caption(f"Published version: {version}")
            df = display_schedule(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            show_breaks_for_days(DAYS)
            st.download_button("Download published timetable CSV", data=df.to_csv(index=False).encode("utf-8"), file_name=f"timeweaver_published_v{version}.csv", mime="text/csv")

elif role == "teacher":
    page = st.sidebar.radio("Teacher Portal", ["My Timetable", "My Profile"])
    if page == "My Timetable":
        st.header("👨‍🏫 Teacher Portal")
        st.write("This view is filtered by your authenticated teacher account.")
        rows = schedule_rows_for_user("teacher", user)
        if not rows:
            st.info("No published classes are assigned to your account yet. Ask the admin to assign sessions and publish a timetable.")
        else:
            df = display_schedule(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            show_breaks_for_days(DAYS)
            st.download_button("Download my timetable CSV", data=df.to_csv(index=False).encode("utf-8"), file_name="my_teacher_timetable.csv", mime="text/csv")
            st.subheader("Daily schedule")
            available_days = list(dict.fromkeys(df["Day"].tolist()))
            selected_day = st.selectbox("Choose a day", available_days)
            st.dataframe(df[df["Day"] == selected_day], use_container_width=True, hide_index=True)
            show_breaks_for_days([selected_day])
    else:
        st.header("👤 My Teacher Profile")
        profile = db_one("SELECT teacher_id, teacher_name FROM teachers WHERE teacher_id=?", (user["teacher_id"],))
        st.write(f"**Username:** {user['username']}")
        st.write(f"**Teacher ID:** {user['teacher_id']}")
        st.write(f"**Name:** {profile['teacher_name'] if profile else 'Not found'}")
        st.caption("Teacher accounts have read-only access to published timetable records.")

elif role == "student":
    page = st.sidebar.radio("Student Portal", ["My Class Timetable", "My Profile"])
    if page == "My Class Timetable":
        st.header("🎓 Student Portal")
        st.write("This view is filtered by the student group linked to your account.")
        rows = schedule_rows_for_user("student", user)
        if not rows:
            st.info("No published timetable is available for your group yet. Ask the admin to publish one.")
        else:
            df = display_schedule(rows)
            st.caption(f"Authorised group: {user['student_group']}")
            st.dataframe(df, use_container_width=True, hide_index=True)
            show_breaks_for_days(DAYS)
            st.download_button("Download my class timetable CSV", data=df.to_csv(index=False).encode("utf-8"), file_name=f"{user['student_group']}_timetable.csv", mime="text/csv")
            selected_day = st.selectbox("Daily view", list(dict.fromkeys(df["Day"].tolist())))
            st.dataframe(df[df["Day"] == selected_day], use_container_width=True, hide_index=True)
            show_breaks_for_days([selected_day])
    else:
        st.header("👤 My Student Profile")
        group = db_one("SELECT group_id, branch, strength FROM student_groups WHERE group_id=?", (user["student_group"],))
        st.write(f"**Username:** {user['username']}")
        st.write(f"**Student group:** {user['student_group']}")
        if group:
            st.write(f"**Branch:** {group['branch']}")
            st.write(f"**Registered group strength:** {group['strength']}")
        st.caption("Students can only view published timetable records for their assigned group.")

else:
    st.error("This account has an unsupported role. Contact the administrator.")
    if st.button("Log out"):
        st.session_state.auth_user = None
        st.rerun()
