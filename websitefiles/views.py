from flask import Blueprint, redirect, render_template, request, session, url_for

from tutormatch import (
    GRADE_LEVELS,
    QUIZ_QUESTIONS,
    SUBJECTS,
    TEACHING_QUESTIONS,
    build_profile_sentence,
    clean_answers,
    get_profile,
    is_onboarded,
    match_for_answers,
    save_learner_profile,
    save_tutor_profile,
    set_user_role,
    style_from_teaching_answers,
)

from .auth import login_required

views = Blueprint("views", __name__)

_LEVELS = {o.value for o in GRADE_LEVELS}
_SUBJECTS = {o.value for o in SUBJECTS}
MAX_RATE_SOL = 10


# "/" is listed last so it registers first and becomes the canonical address -
# decorators apply bottom-up. Otherwise url_for("views.home") builds "/home".
@views.route("/home")
@views.route("/")
def home():
    # Public on purpose: logout returns here. Logged-in people who haven't set
    # up a profile yet are sent to onboarding, so nobody skips it.
    user = session.get("user")
    if user and not is_onboarded(user["sub"]):
        return redirect(url_for("views.onboarding"))
    return render_template("home.html")


def _prefixed(form, prefix):
    """Form fields for one quiz. Both quizzes share question ids, so each is prefixed."""
    return {key[len(prefix):]: form.get(key) for key in form if key.startswith(prefix)}


def _unanswered(answers, questions):
    return [q.id for q in questions if not q.free_text and q.id not in answers]


def _values_from_profile(profile):
    """Pre-fill the form with what's already saved, so onboarding doubles as 'edit profile'."""
    if not profile:
        return {}
    role = profile["user"]["role"]
    if role == "tutor" and profile["tutor"]:
        t = profile["tutor"]
        style = t["style_affinity"] or {}
        return {
            "role": "tutor",
            "subjects": t["subjects"] or [],
            "teaching_levels": t["teaching_levels"] or [],
            "bio": t["bio"] or "",
            "hourly_rate_sol": t["hourly_rate_sol"],
            # The picked answer is the one scored 1.0.
            **{f"teach_{axis}": max(scores, key=scores.get) for axis, scores in style.items() if scores},
        }
    if profile["learner"]:
        l = profile["learner"]
        return {
            "role": "student",
            "subjects": l["subjects"] or [],
            "grade_level": l["grade_level"],
            **{f"learn_{k}": v for k, v in (l["raw_answers"] or {}).items()},
        }
    return {}


@views.route("/onboarding", methods=["GET", "POST"])
@login_required
def onboarding():
    sub = session["user"]["sub"]
    errors = []

    if request.method == "POST":
        form = request.form
        role = form.get("role")
        subjects = [s for s in form.getlist("subjects") if s in _SUBJECTS]

        if role not in ("student", "tutor"):
            errors.append("Choose whether you're a student or a tutor.")
        if not subjects:
            errors.append("Pick at least one subject.")

        if role == "student":
            grade = form.get("grade_level")
            answers = clean_answers(_prefixed(form, "learn_"), QUIZ_QUESTIONS)
            if grade not in _LEVELS:
                errors.append("Choose your grade level.")
            if _unanswered(answers, QUIZ_QUESTIONS):
                errors.append("Answer every question in the learning style quiz.")
            if not errors:
                set_user_role(sub, "student")
                save_learner_profile(
                    sub,
                    subjects=subjects,
                    pace=answers["pace"],
                    goals=answers.get("goal", ""),
                    raw_answers=answers,
                    profile_sentence=build_profile_sentence(answers, subjects),
                    grade_level=grade,
                )
                session["user"] = {**session["user"], "role": "student"}
                return redirect(url_for("views.matches"))

        elif role == "tutor":
            levels = [lvl for lvl in form.getlist("teaching_levels") if lvl in _LEVELS]
            answers = clean_answers(_prefixed(form, "teach_"), TEACHING_QUESTIONS)
            bio = (form.get("bio") or "").strip()[:1000]
            try:
                rate = round(float(form.get("hourly_rate_sol", "")), 4)
            except ValueError:
                rate = None
            if not levels:
                errors.append("Pick at least one level you teach.")
            if _unanswered(answers, TEACHING_QUESTIONS):
                errors.append("Answer every question in the teaching style quiz.")
            if len(bio) < 10:
                errors.append("Write a short bio (at least 10 characters).")
            if rate is None or not 0 < rate <= MAX_RATE_SOL:
                errors.append(f"Enter an hourly rate between 0 and {MAX_RATE_SOL} SOL.")
            if not errors:
                set_user_role(sub, "tutor")
                save_tutor_profile(
                    sub,
                    bio=bio,
                    subjects=subjects,
                    teaching_levels=levels,
                    style_affinity=style_from_teaching_answers(answers),
                    hourly_rate_sol=rate,
                )
                session["user"] = {**session["user"], "role": "tutor"}
                return redirect(url_for("views.home"))

        # Re-show the form with what they entered.
        values = {k: form.get(k) for k in form}
        values["subjects"] = form.getlist("subjects")
        values["teaching_levels"] = form.getlist("teaching_levels")
    else:
        values = _values_from_profile(get_profile(sub))

    return render_template(
        "onboarding.html",
        errors=errors,
        values=values,
        grade_levels=GRADE_LEVELS,
        subjects=SUBJECTS,
        quiz_questions=QUIZ_QUESTIONS,
        teaching_questions=TEACHING_QUESTIONS,
        max_rate=MAX_RATE_SOL,
    )


@views.route("/matches")
@login_required
def matches():
    profile = get_profile(session["user"]["sub"])
    if not profile or profile["user"]["role"] == "tutor":
        return redirect(url_for("views.home"))
    learner = profile["learner"]
    if not learner:
        return redirect(url_for("views.onboarding"))

    sentence, results = match_for_answers(
        learner["raw_answers"] or {},
        learner["subjects"] or [],
        grade_level=learner["grade_level"],
        limit=3,
    )
    labels = {o.value: o.label for o in GRADE_LEVELS + SUBJECTS}
    return render_template(
        "matches.html",
        sentence=sentence,
        matches=results,
        grade_label=labels.get(learner["grade_level"], ""),
        labels=labels,
    )
