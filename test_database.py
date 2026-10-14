from database import (
    init_db,
    seed_demo_data,
    authenticate_user
)

init_db()
seed_demo_data()

# Test admin login
admin = authenticate_user(
    "admin",
    "ChangeMe_Admin_2026!"
)

assert admin is not None
assert admin["role"] == "admin"
print("PASS: Admin login")

# Test teacher login
teacher = authenticate_user(
    "teacher1",
    "ChangeMe_Teacher_2026!"
)

assert teacher is not None
assert teacher["role"] == "teacher"
print("PASS: Teacher login")

# Test student login
student = authenticate_user(
    "student1",
    "ChangeMe_Student_2026!"
)

assert student is not None
assert student["role"] == "student"
print("PASS: Student login")

# Test incorrect password
wrong_login = authenticate_user(
    "admin",
    "wrong_password"
)

assert wrong_login is None
print("PASS: Incorrect password rejected")

print("\nAll database tests passed!")