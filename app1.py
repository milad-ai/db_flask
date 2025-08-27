import os
import re
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session
from sqlalchemy import create_engine, text

# ==================== تنظیمات ====================
DB_URI = os.environ.get("DB_URI")
if not DB_URI:
    DB_URI = "sqlite:///./local_test.db"

engine = create_engine(DB_URI)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# برای دکمه‌ها
MAJORS = ["علوم کامپیوتر", "آمار"]
HW_NUMBERS = ["3", "4", "5", "6"]

WELCOME_MD = (
    "نمونه ارسال درست:\n\n"
    "\n# number 1\nSELECT id, name FROM students;\n\n# number 2\nSELECT COUNT(*) FROM students;\n\n"
)

def parse_queries(sql_text: str):
    splits = re.split(r"#\s*number\s*\d+", sql_text, flags=re.IGNORECASE)
    queries = [q.strip().rstrip(";") + ";" for q in splits if q.strip()]
    return queries

def get_submission_count(student_id: str, hw: str) -> int:
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    SELECT COUNT(*) 
                    FROM student_results 
                    WHERE student_id = :student_id AND hw = :hw
                """),
                {"student_id": student_id, "hw": hw},
            ).fetchone()
            return int(result[0]) if result else 0
    except Exception as e:
        app.logger.error(f"Error getting submission count: {e}")
        return 0

@app.route("/")
def index():
    return render_template(
        "index.html",
        majors=MAJORS,
        hw_numbers=HW_NUMBERS,
        welcome_md=WELCOME_MD,
    )

@app.route("/submit", methods=["GET", "POST"])
def submit():
    if request.method == "GET":
        return render_template("submit.html", majors=MAJORS, hw_numbers=HW_NUMBERS)

    name = request.form.get("name", "").strip()
    student_id = request.form.get("student_id", "").strip()
    major = request.form.get("major")
    hw = request.form.get("hw")
    sql_text = request.form.get("sql_text", "")
    file = request.files.get("sql_file")

    if not name or not student_id or major not in MAJORS or hw not in HW_NUMBERS:
        flash("لطفاً همه فیلدها را به‌درستی پر کنید.", "danger")
        return redirect(url_for("submit"))

    # محدودیت دفعات ارسال
    submission_count = get_submission_count(student_id, hw)
    if submission_count >= 10:
        flash(f"شما قبلاً ۱۰ بار تمرین {hw} را ارسال کرده‌اید و مجاز به ارسال مجدد نیستید.", "warning")
        return redirect(url_for("index"))

    # دریافت SQL از فایل یا تکست
    if file and file.filename:
        if not file.filename.lower().endswith(".sql"):
            flash("لطفاً فایل .sql معتبر ارسال کنید.", "danger")
            return redirect(url_for("submit"))
        sql_text = file.stream.read().decode("utf-8")

    if not sql_text.strip():
        flash("متن SQL خالی است.", "danger")
        return redirect(url_for("submit"))

    queries = parse_queries(sql_text)

    correct_count = 0
    incorrect_questions = []

    with engine.begin() as conn:
        # ایجاد جدول نتایج در صورت نبود (با ستون major)
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS student_results (
                id SERIAL PRIMARY KEY,
                student_id TEXT NOT NULL,
                name TEXT NOT NULL,
                major TEXT NOT NULL,
                hw TEXT NOT NULL,
                correct_count INTEGER NOT NULL,
                submission_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        ))

        # انتخاب پسوند جدول مرجع بر اساس رشته
        suffix = "stat" if major == "آمار" else "cs"

        for i, student_query in enumerate(queries):
            qnum = i + 1
            reference_table = f"hw{hw}_q{qnum}_{suffix}_reference"
            try:
                student_rows = conn.execute(text(student_query)).fetchall()
                reference_rows = conn.execute(text(f"SELECT * FROM {reference_table}")).fetchall()
                if set(student_rows) == set(reference_rows):
                    correct_count += 1
                else:
                    incorrect_questions.append(qnum)
            except Exception as e:
                app.logger.error(f"Error executing q{qnum}: {e}")
                incorrect_questions.append(qnum)

        # درج نتیجه
        try:
            conn.execute(
                text(
                    """
                    INSERT INTO student_results (student_id, name, major, hw, correct_count)
                    VALUES (:student_id, :name, :major, :hw, :correct_count)
                    """
                ),
                {
                    "student_id": student_id,
                    "name": name,
                    "major": major,
                    "hw": hw,
                    "correct_count": correct_count,
                },
            )
        except Exception as e:
            flash(f"خطای ذخیره‌سازی نتیجه: {e}", "danger")
            return redirect(url_for("submit"))

    new_submission_count = submission_count + 1
    remaining = 10 - new_submission_count

    session["result"] = {
        "name": name,
        "student_id": student_id,
        "major": major,
        "hw": hw,
        "total": len(queries),
        "correct": correct_count,
        "incorrect": incorrect_questions,
        "done": new_submission_count,
        "remaining": remaining,
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    return redirect(url_for("result"))

@app.route("/result")
def result():
    data = session.get("result")
    if not data:
        return redirect(url_for("index"))
    return render_template("result.html", **data)

@app.route("/admin/stats")
def admin_stats():
    # آمار تفکیک‌شده بر اساس رشته و تمرین
    try:
        with engine.begin() as conn:
            rows = conn.execute(text(
                """
                SELECT major, hw, COUNT(*) AS submissions, AVG(correct_count) AS avg_correct
                FROM student_results
                GROUP BY major, hw
                ORDER BY major, hw
                """
            )).mappings().all()
    except Exception as e:
        flash(f"خطا در بارگذاری آمار: {e}", "danger")
        rows = []
    return render_template("admin_stats.html", rows=rows)
