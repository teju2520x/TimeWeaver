
from scheduler import is_valid, generate_timetable

context = {
    "opening": "09:00",
    "closing": "13:00",
    "rooms": {
        "R101": 60,
        "R102": 30
    },
    "breaks": [
        {
            "name": "Tea Break",
            "start": "10:30",
            "end": "10:45",
            "days": ["Monday"]
        }
    ]
}

first_class = {
    "id": "S1",
    "subject": "Python",
    "day": "Monday",
    "start": "09:00",
    "end": "10:00",
    "teacher": "T1",
    "group": "CSE-A",
    "room": "R101",
    "strength": 50,
    "priority": 3
}

assert is_valid(first_class, [], context)
print("PASS: Valid assignment")

# Room capacity violation.
bad_capacity = {**first_class, "strength": 70}
assert not is_valid(bad_capacity, [], context)
print("PASS: Capacity violation rejected")

# Teacher clash.
other_class = {
    **first_class,
    "id": "S2",
    "subject": "Mathematics",
    "teacher": "T1",
    "group": "CSE-B",
    "room": "R102"
}
assert not is_valid(other_class, [first_class], context)
print("PASS: Teacher clash rejected")

# Break clash.
break_class = {
    **first_class,
    "start": "10:15",
    "end": "11:00"
}
assert not is_valid(break_class, [], context)
print("PASS: Break clash rejected")

# Adjacent classes are allowed.
adjacent_class = {
    **first_class,
    "id": "S3",
    "start": "10:00",
    "end": "10:30"
}
assert is_valid(adjacent_class, [first_class], context)
print("PASS: Adjacent intervals accepted")

# Generate a complete timetable.
sessions = [
    {
        "id": "S1",
        "subject": "Python",
        "teacher": "T1",
        "group": "CSE-A",
        "duration": 60,
        "strength": 50,
        "priority": 3
    },
    {
        "id": "S2",
        "subject": "Mathematics",
        "teacher": "T2",
        "group": "CSE-A",
        "duration": 60,
        "strength": 50,
        "priority": 3
    },
    {
        "id": "S3",
        "subject": "English",
        "teacher": "T1",
        "group": "CSE-B",
        "duration": 60,
        "strength": 25,
        "priority": 1
    }
]

schedule, status = generate_timetable(
    sessions,
    ["Monday", "Tuesday"],
    ["09:00", "10:00", "11:00", "12:00"],
    context
)

assert status == "VALID", f"Generation failed: {status}"
assert len(schedule) == len(sessions)

print("PASS: Backtracking generated a complete timetable")
print("\nGenerated timetable:")

for item in schedule:
    print(
        item["day"],
        item["start"] + "-" + item["end"],
        item["subject"],
        "| Teacher:", item["teacher"],
        "| Group:", item["group"],
        "| Room:", item["room"]
    )