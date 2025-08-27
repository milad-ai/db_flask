
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

MAJORS = ["علوم کامپیوتر", "آمار"]
HW_NUMBERS = ["3", "4", "5", "6"]

WELCOME_MD = (
    "نمونه ارسال درست:\n\n"
    "\n# number 1\nSELECT id, name FROM students;\n\n# number 2\nSELECT COUNT(*) FROM students;\n\n"
)

# ==================== توابع کمکی ====================
def parse_queries(sql_text: str):
    splits = re.split(r"#\s*number\s*\d+", sql_text, flags=re.IGNORECASE)
    queries = [q.strip().rstrip(";") + ";" for q in splits if q.strip()]
    return queries

def get_submission_count(student_id: str, hw: str) -> int:
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM student_results WHERE student_id = :student_id AND hw = :hw"),
                {"student_id": student_id, "hw": hw},
            ).fetchone()
            return int(result[0]) if result else 0
    except Exception as e:
        app.logger.error(f"Error getting submission count: {e}")
        return 0

def get_student_info(student_id: str, password: str = None):
    try:
        with engine.begin() as conn:
            if password is None:
                # فقط بررسی شماره دانشجویی
                result = conn.execute(
                    text("SELECT name, major, pass FROM stuid WHERE student_id = :student_id"),
                    {"student_id": student_id}
                ).fetchone()
                if result:
                    return result[0], result[1], result[2]  # name, major, password
                return None, None, None
            else:
                # بررسی شماره دانشجویی + پسورد
                result = conn.execute(
                    text("SELECT name, major FROM stuid WHERE student_id = :student_id AND pass = :password"),
                    {"student_id": student_id, "password": password}
                ).fetchone()
                if result:
                    return result[0], result[1]
                return None, None
    except Exception as e:
        app.logger.error(f"Error getting student info: {e}")
        return None, None

def save_submission(student_id, name, major, hw, correct_count):
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS student_results (
                    id SERIAL PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    major TEXT NOT NULL,
                    hw TEXT NOT NULL,
                    correct_count INTEGER NOT NULL,
                    submission_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(
                text("INSERT INTO student_results (student_id, name, major, hw, correct_count) VALUES (:student_id, :name, :major, :hw, :correct_count)"),
                {"student_id": student_id, "name": name, "major": major, "hw": hw, "correct_count": correct_count}
            )
            return True
    except Exception as e:
        app.logger.error(f"Error saving submission: {e}")
        return False

# ==================== مسیرها ====================

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        student_id = request.form.get("student_id").strip()
        password = request.form.get("password").strip()
        name, major = get_student_info(student_id, password)
        if name and major:
            session["student_id"] = student_id
            session["name"] = name
            session["major"] = major
            return redirect(url_for("home"))
        else:
            flash("شماره دانشجویی یا رمز عبور اشتباه است!", "danger")
    return render_template("login.html")

@app.route("/home")
def home():
    if "student_id" not in session:
        return redirect(url_for("login"))
    return render_template("home.html", hw_numbers=HW_NUMBERS)

@app.route("/submit/<hw>", methods=["GET", "POST"])
def submit(hw):
    if "student_id" not in session:
        return redirect(url_for("login"))
    
    student_id = session["student_id"]
    name = session["name"]
    major = session["major"]

    submission_count = get_submission_count(student_id, hw)
    if submission_count >= 10:
        flash(f"شما قبلاً ۱۰ بار تمرین {hw} را ارسال کرده‌اید.", "warning")
        return redirect(url_for("home"))

    if request.method == "POST":
        sql_text = request.form.get("sql_text", "")
        queries = parse_queries(sql_text)
        correct_count = 0
        incorrect_questions = []

        with engine.begin() as conn:
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

            saved = save_submission(student_id, name, major, hw, correct_count)
            if not saved:
                flash("خطا در ذخیره‌سازی تمرین!", "danger")
                return redirect(url_for("home"))

        session["result"] = {
            "name": name,
            "student_id": student_id,
            "major": major,
            "hw": hw,
            "total": len(queries),
            "correct": correct_count,
            "incorrect": incorrect_questions,
            "done": submission_count + 1,
            "remaining": 10 - (submission_count + 1),
        }
        return redirect(url_for("result"))

    return render_template("submit_hw.html", hw=hw)

@app.route("/result")
def result():
    data = session.get("result")
    if not data:
        return redirect(url_for("home"))
    return render_template("result.html", **data)

@app.route("/admin/stats")
def admin_stats():
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT major, hw, COUNT(*) AS submissions, AVG(correct_count) AS avg_correct
                FROM student_results
                GROUP BY major, hw
                ORDER BY major, hw
            """)).mappings().all()
    except Exception as e:
        flash(f"خطا در بارگذاری آمار: {e}", "danger")
        rows = []
    return render_template("admin_stats.html", rows=rows)

# ==================== اجرای سرور ====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
