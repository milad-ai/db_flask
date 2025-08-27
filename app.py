
import os
import re
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session
from sqlalchemy import create_engine, text

# ==================== تنظیمات ====================
DB_URI = os.environ.get("DB_URI", "sqlite:///./local_test.db")
engine = create_engine(DB_URI, pool_pre_ping=True)


try:
    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))
except:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# دکمه‌ها و رشته‌ها
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

def authenticate(student_id: str, password: str):
    """بررسی شماره دانشجویی و پسورد و برگرداندن نام و رشته"""
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT name, major FROM stuid WHERE student_id=:sid AND pass=:pwd"),
                {"sid": student_id, "pwd": password}
            ).fetchone()
            if row:
                return row[0], row[1]  # name, major
            return None, None
    except Exception as e:
        app.logger.error(f"Auth error: {e}")
        return None, None

# ==================== روت‌ها ====================

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        password = request.form.get("password", "").strip()
        if not student_id or not password:
            flash("لطفاً شماره دانشجویی و رمز عبور را وارد کنید.", "danger")
            return redirect(url_for("login"))

        name, major = authenticate(student_id, password)
        if not name:
            flash("شماره دانشجویی یا رمز عبور اشتباه است.", "danger")
            return redirect(url_for("login"))

        session["student_id"] = student_id
        session["name"] = name
        session["major"] = major
        return redirect(url_for("dashboard"))
    
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "student_id" not in session:
        flash("ابتدا وارد شوید.", "warning")
        return redirect(url_for("login"))
    return render_template(
        "dashboard.html",
        name=session["name"],
        student_id=session["student_id"],
        major=session["major"]
    )

@app.route("/submit", methods=["GET", "POST"])
def submit():
    if "student_id" not in session:
        flash("لطفاً ابتدا وارد شوید.", "warning")
        return redirect(url_for("login"))

    student_id = session["student_id"]
    name = session["name"]
    major = session["major"]

    if request.method == "GET":
        return render_template("submit.html", majors=MAJORS, hw_numbers=HW_NUMBERS, name=name, student_id=student_id, major=major)

    hw = request.form.get("hw")
    sql_text = request.form.get("sql_text", "")
    file = request.files.get("sql_file")

    if hw not in HW_NUMBERS:
        flash("تمرین معتبر انتخاب کنید.", "danger")
        return redirect(url_for("submit"))

    submission_count = get_submission_count(student_id, hw)
    if submission_count >= 10:
        flash(f"شما قبلاً ۱۰ بار تمرین {hw} را ارسال کرده‌اید.", "warning")
        return redirect(url_for("submit"))

    # دریافت SQL
    if file and file.filename:
        if not file.filename.lower().endswith(".sql"):
            flash("فایل معتبر .sql ارسال کنید.", "danger")
            return redirect(url_for("submit"))
        sql_text = file.stream.read().decode("utf-8")

    if not sql_text.strip():
        flash("متن SQL خالی است.", "danger")
        return redirect(url_for("submit"))

    queries = parse_queries(sql_text)
    correct_count = 0
    incorrect_questions = []

    with engine.begin() as conn:
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

        conn.execute(
            text(
                "INSERT INTO student_results (student_id, name, major, hw, correct_count) "
                "VALUES (:student_id, :name, :major, :hw, :correct_count)"
            ),
            {"student_id": student_id, "name": name, "major": major, "hw": hw, "correct_count": correct_count},
        )

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

@app.route("/register_email", methods=["GET", "POST"])
def register_email():
    student_id = session.get("student_id")
    if not student_id:
        flash("ابتدا وارد شوید.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email:
            flash("لطفاً ایمیل را وارد کنید.", "danger")
            return redirect(url_for("register_email"))

        try:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE stuid SET email = :email WHERE student_id = :student_id"),
                    {"email": email, "student_id": student_id}
                )
            flash("ایمیل شما با موفقیت ثبت شد.", "success")
        except Exception as e:
            flash(f"خطا در ثبت ایمیل: {e}", "danger")
        return redirect(url_for("register_email"))

    email_value = None
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT email FROM stuid WHERE student_id = :student_id"),
            {"student_id": student_id}
        ).fetchone()
        if row:
            email_value = row[0]

    return render_template("register_email.html", student_id=student_id, email_value=email_value)

@app.route("/result")
def result():
    data = session.get("result")
    if not data:
        return redirect(url_for("submit"))
    return render_template("result.html", **data)

@app.route("/admin/stats")
def admin_stats():
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

@app.route("/logout")
def logout():
    session.clear()
    flash("با موفقیت خارج شدید.", "success")
    return redirect(url_for("login"))
@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    if "student_id" not in session:
        flash("ابتدا وارد شوید.", "warning")
        return redirect(url_for("login"))

    student_id = session["student_id"]

    if request.method == "POST":
        old_pass = request.form.get("old_password", "").strip()
        new_pass = request.form.get("new_password", "").strip()
        confirm_pass = request.form.get("confirm_password", "").strip()

        if not old_pass or not new_pass or not confirm_pass:
            flash("لطفاً همه فیلدها را پر کنید.", "danger")
            return redirect(url_for("change_password"))

        if new_pass != confirm_pass:
            flash("رمز جدید و تکرار آن مطابقت ندارند.", "danger")
            return redirect(url_for("change_password"))

        try:
            with engine.begin() as conn:
                # بررسی رمز قبلی
                row = conn.execute(
                    text("SELECT pass FROM stuid WHERE student_id=:sid"),
                    {"sid": student_id}
                ).fetchone()
                if not row or row[0] != old_pass:
                    flash("رمز قبلی اشتباه است.", "danger")
                    return redirect(url_for("change_password"))

                # بروزرسانی رمز
                conn.execute(
                    text("UPDATE stuid SET pass=:new_pass WHERE student_id=:sid"),
                    {"new_pass": new_pass, "sid": student_id}
                )
            flash("رمز عبور با موفقیت تغییر کرد.", "success")
            return redirect(url_for("dashboard"))
        except Exception as e:
            flash(f"خطا در تغییر رمز عبور: {e}", "danger")
            return redirect(url_for("change_password"))

    return render_template("change_password.html")
