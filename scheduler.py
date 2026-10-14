
from datetime import datetime, time


def to_minutes(value):
    """Convert a time object or HH:MM string into minutes after midnight."""
    if isinstance(value, time):
        return value.hour * 60 + value.minute

    if isinstance(value, str):
        try:
            parsed = datetime.strptime(value, "%H:%M").time()
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            raise ValueError(f"Invalid time: {value}. Use HH:MM.")

    raise ValueError("Time must be a time object or HH:MM string.")


def overlaps(start1, end1, start2, end2):
    """Return True when two time intervals overlap."""
    return start1 < end2 and start2 < end1


def is_valid(candidate, current_schedule, context):
    """
    Check whether a proposed class assignment satisfies
    the hard scheduling constraints.

    candidate:
        A dictionary containing day, start, end, teacher,
        group, room, and strength.

    current_schedule:
        List of previously accepted assignments.

    context:
        A dictionary containing rooms, breaks, and
        college opening/closing times.
    """
    required = {
        "day", "start", "end", "teacher",
        "group", "room", "strength"
    }

    if not isinstance(candidate, dict):
        return False

    if not required.issubset(candidate):
        return False

    try:
        start = to_minutes(candidate["start"])
        end = to_minutes(candidate["end"])
        strength = int(candidate["strength"])
    except (ValueError, TypeError, OverflowError):
        return False

    if start >= end or strength <= 0:
        return False

    if not candidate["teacher"] or not candidate["group"]:
        return False

    if not isinstance(current_schedule, list):
        return False

    if not isinstance(context, dict):
        return False

    # Check college working hours.
    try:
        opening = to_minutes(context["opening"])
        closing = to_minutes(context["closing"])
    except (KeyError, ValueError, TypeError):
        return False

    if start < opening or end > closing:
        return False

    # Check that the room exists and has enough capacity.
    rooms = context.get("rooms", {})

    if candidate["room"] not in rooms:
        return False

    try:
        capacity = int(rooms[candidate["room"]])
    except (ValueError, TypeError, OverflowError):
        return False

    if capacity <= 0 or capacity < strength:
        return False

    # Check previously scheduled classes.
    for existing in current_schedule:
        if existing.get("day") != candidate["day"]:
            continue

        try:
            other_start = to_minutes(existing["start"])
            other_end = to_minutes(existing["end"])
        except (KeyError, ValueError, TypeError):
            return False

        if overlaps(start, end, other_start, other_end):

            # A teacher cannot teach two classes at once.
            if existing.get("teacher") == candidate["teacher"]:
                return False

            # A room cannot host two classes at once.
            if existing.get("room") == candidate["room"]:
                return False

            # A student group cannot attend two classes at once.
            if existing.get("group") == candidate["group"]:
                return False

    # Check applicable breaks.
    for br in context.get("breaks", []):
        if candidate["day"] not in br.get("days", []):
            continue

        break_start = to_minutes(br["start"])
        break_end = to_minutes(br["end"])

        if break_start >= break_end:
            return False

        if overlaps(start, end, break_start, break_end):
            return False

    return True


def generate_timetable(sessions, days, start_times, context):
    """
    Generate a timetable using backtracking.

    Each session must contain:
        id, subject, teacher, group, duration, strength, priority

    start_times contains candidate start times as HH:MM strings.
    Returns (schedule, status).
    """
    if not sessions:
        return [], "VALID"

    if not days or not start_times:
        return [], "UNSAT"

    # Fail fast on malformed or duplicate sessions.
    ids = [session.get("id") for session in sessions]

    if (
        any(not isinstance(sid, str) or not sid for sid in ids)
        or len(set(ids)) != len(ids)
    ):
        return [], "UNSAT"

    if any(
        not isinstance(session.get("duration"), int)
        or isinstance(session.get("duration"), bool)
        or session["duration"] <= 0
        for session in sessions
    ):
        return [], "UNSAT"

    # Most constrained sessions first: duration, then priority.
    remaining = sorted(
        sessions,
        key=lambda s: (
            -s["duration"],
            -s.get("priority", 1)
        )
    )

    schedule = []

    def backtrack(index):
        if index == len(remaining):
            return True

        session = remaining[index]

        # High-priority sessions prefer earlier start times.
        ordered_starts = sorted(
            start_times,
            key=to_minutes
        )

        for day in days:
            for start_value in ordered_starts:
                try:
                    start_min = to_minutes(start_value)
                    end_min = start_min + session["duration"]
                    end_value = (
                        f"{end_min // 60:02d}:"
                        f"{end_min % 60:02d}"
                    )
                except (ValueError, TypeError):
                    continue

                # Do not silently create times beyond midnight.
                if end_min >= 24 * 60:
                    continue

                for room_id in context.get("rooms", {}):
                    candidate = {
                        "id": session["id"],
                        "subject": session["subject"],
                        "day": day,
                        "start": start_value,
                        "end": end_value,
                        "teacher": session["teacher"],
                        "group": session["group"],
                        "room": room_id,
                        "strength": session["strength"],
                        "priority": session.get("priority", 1)
                    }

                    # Every proposed assignment passes through is_valid().
                    if is_valid(candidate, schedule, context):
                        schedule.append(candidate)

                        if backtrack(index + 1):
                            return True

                        # Undo the assignment when it leads to a dead end.
                        schedule.pop()

        return False

    if backtrack(0):
        return schedule, "VALID"

    return [], "UNSAT"
