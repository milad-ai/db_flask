

import os
import re
from datetime import datetime
import pytz
import jdatetime
from flask import Flask, render_template, request, redirect, url_for, flash, session
from sqlalchemy import create_engine, text
import json
# ==================== تنظیمات ====================
DB_URI = os.environ.get("DB_URI", "sqlite:///./local_test.db")
engine = create_engine(DB_URI, pool_pre_ping=True)
# اضافه کردن در ابتدای فایل
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

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

def utc_to_tehran(utc_dt):
    """تبدیل زمان UTC به ساعت تهران"""
    try:
        if isinstance(utc_dt, str):
            utc_dt = datetime.strptime(utc_dt, "%Y-%m-%d %H:%M:%S")
        
        utc_zone = pytz.utc
        tehran_zone = pytz.timezone('Asia/Tehran')
        
        if utc_dt.tzinfo is None:
            utc_dt = utc_zone.localize(utc_dt)
        
        tehran_dt = utc_dt.astimezone(tehran_zone)
        return tehran_dt
    except Exception as e:
        app.logger.error(f"Error in utc_to_tehran: {e}")
        return utc_dt

def gregorian_to_jalali_fa(dt):
    """تبدیل تاریخ میلادی به شمسی فارسی"""
    try:
        if isinstance(dt, str):
            dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
        
        jdate = jdatetime.date.fromgregorian(date=dt.date())
        day = jdate.day
        month_name = jdate.j_months_fa[jdate.month - 1]
        year = jdate.year
        return f"{day} {month_name} {year}"
    except Exception as e:
        app.logger.error(f"Error in gregorian_to_jalali_fa: {e}")
        return "خطا در تبدیل تاریخ"

def format_datetime_fa(dt):
    """فرمت‌بندی تاریخ و زمان به فارسی"""
    try:
        tehran_dt = utc_to_tehran(dt)
        date_fa = gregorian_to_jalali_fa(tehran_dt)
        time_str = tehran_dt.strftime("%H:%M")
        return f"{date_fa} - {time_str}"
    except Exception as e:
        app.logger.error(f"Error in format_datetime_fa: {e}")
        return "خطا در تبدیل تاریخ"

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
    
    # ذخیره زمان به صورت رشته برای جلوگیری از مشکلات serialization
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
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
        "time": current_time,  # ذخیره به صورت رشته
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
    
    # تبدیل زمان به فرمت فارسی
    if "time" in data and data["time"]:
        try:
            data["time_fa"] = format_datetime_fa(data["time"])
        except Exception as e:
            app.logger.error(f"Error converting time in result route: {e}")
            data["time_fa"] = f"خطا در تبدیل تاریخ: {str(e)}"
    else:
        data["time_fa"] = "نامشخص"
    
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

@app.route("/run_test_query", methods=["GET", "POST"])
def run_test_query():
    if "student_id" not in session:
        flash("ابتدا وارد شوید.", "warning")
        return redirect(url_for("login"))

    output = None
    query_text = ""

    if request.method == "POST":
        query_text = request.form.get("query", "").strip()

        # فقط SELECT مجاز است
        if not query_text.lower().startswith("select"):
            flash("فقط دستورات SELECT مجاز است.", "danger")
            return redirect(url_for("run_test_query"))

        # مطمئن شو فقط روی جدول test اجرا میشه
        if "test" not in query_text.lower():
            flash("تنها جدول 'test' قابل استفاده است.", "danger")
            return redirect(url_for("run_test_query"))

        try:
            with engine.begin() as conn:
                result = conn.execute(text(query_text))
                columns = result.keys()
                rows = result.fetchall()
                output = {"columns": columns, "rows": rows}
        except Exception as e:
            flash(f"خطا در اجرای SQL: {e}", "danger")

    return render_template("test_sql_runner.html", output=output, query=query_text)

# ==================== روت‌های ادمین ====================


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            flash("ورود ادمین موفقیت‌آمیز بود.", "success")
            return redirect(url_for("admin_dashboard"))
        else:
            flash("نام کاربری یا رمز عبور اشتباه است.", "danger")
    
    return render_template("admin_login.html")

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin_logged_in"):
        flash("لطفاً به عنوان ادمین وارد شوید.", "warning")
        return redirect(url_for("admin_login"))
    
    return render_template("admin_dashboard.html")

@app.route("/admin/query", methods=["GET", "POST"])
def admin_query():
    if not session.get("admin_logged_in"):
        flash("لطفاً به عنوان ادمین وارد شوید.", "warning")
        return redirect(url_for("admin_login"))
    
    output = None
    query_text = ""
    
    if request.method == "POST":
        query_text = request.form.get("query", "").strip()
        
        if not query_text:
            flash("لطفاً یک کوئری وارد کنید.", "danger")
            return redirect(url_for("admin_query"))
        
        try:
            with engine.begin() as conn:
                result = conn.execute(text(query_text))
                
                # اگر کوئری داده برمی‌گرداند
                if result.returns_rows:
                    columns = result.keys()
                    rows = result.fetchall()
                    output = {"columns": columns, "rows": rows}
                    flash("کوئری با موفقیت اجرا شد.", "success")
                else:
                    flash("کوئری اجرا شد (هیچ داده‌ای برگردانده نشد).", "info")
                    
        except Exception as e:
            flash(f"خطا در اجرای کوئری: {str(e)}", "danger")
    
    return render_template("admin_query.html", output=output, query=query_text)

@app.route("/admin/submissions")
def admin_submissions():
    if not session.get("admin_logged_in"):
        flash("لطفاً به عنوان ادمین وارد شوید.", "warning")
        return redirect(url_for("admin_login"))
    
    major = request.args.get("major", "")
    hw = request.args.get("hw", "")
    
    try:
        with engine.begin() as conn:
            # گرفتن لیست ارسال‌ها با فیلتر
            query = text("""
                SELECT student_id, name, major, hw, correct_count, submission_time
                FROM student_results
                WHERE 1=1
            """)
            
            params = {}
            
            if major:
                query = text(str(query) + " AND major = :major")
                params["major"] = major
            
            if hw:
                query = text(str(query) + " AND hw = :hw")
                params["hw"] = hw
            
            query = text(str(query) + " ORDER BY submission_time DESC")
            
            # تبدیل به لیست از دیکشنری‌ها
            rows = conn.execute(query, params).fetchall()
            
            # تبدیل به لیست از دیکشنری‌های قابل تغییر
            result_rows = []
            for row in rows:
                row_dict = {
                    "student_id": row[0],
                    "name": row[1],
                    "major": row[2],
                    "hw": row[3],
                    "correct_count": row[4],
                    "submission_time": row[5],
                    "submission_time_fa": format_datetime_fa(row[5]) if row[5] else "نامشخص"
                }
                result_rows.append(row_dict)
                    
    except Exception as e:
        flash(f"خطا در بارگذاری ارسال‌ها: {e}", "danger")
        result_rows = []
    
    return render_template("admin_submissions.html", 
                         rows=result_rows, 
                         majors=MAJORS, 
                         hw_numbers=HW_NUMBERS,
                         selected_major=major,
                         selected_hw=hw)



@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    flash("خروج از پنل ادمین موفقیت‌آمیز بود.", "success")
    return redirect(url_for("admin_login"))
@app.route("/admin/manage_users", methods=["GET", "POST"])
def admin_manage_users():
    if not session.get("admin_logged_in"):
        flash("لطفاً به عنوان مدرس وارد شوید.", "warning")
        return redirect(url_for("admin_login"))
    
    if request.method == "POST":
        student_id = request.form.get("student_id")
        new_password = request.form.get("new_password")
        
        if not student_id or not new_password:
            flash("لطفاً همه فیلدها را پر کنید.", "danger")
            return redirect(url_for("admin_manage_users"))
        
        try:
            with engine.begin() as conn:
                # بررسی وجود دانشجو
                student = conn.execute(
                    text("SELECT student_id, name FROM stuid WHERE student_id = :student_id"),
                    {"student_id": student_id}
                ).fetchone()
                
                if not student:
                    flash("دانشجو با این شماره دانشجویی یافت نشد.", "danger")
                    return redirect(url_for("admin_manage_users"))
                
                # تغییر رمز عبور
                conn.execute(
                    text("UPDATE stuid SET pass = :password WHERE student_id = :student_id"),
                    {"password": new_password, "student_id": student_id}
                )
                
                flash(f"رمز عبور دانشجو {student[1]} با موفقیت تغییر یافت.", "success")
                
        except Exception as e:
            flash(f"خطا در تغییر رمز عبور: {str(e)}", "danger")
    
    # دریافت لیست کاربران
    try:
        with engine.begin() as conn:
            users = conn.execute(
                text("SELECT student_id, name, major, email FROM stuid ORDER BY student_id")
            ).fetchall()
    except Exception as e:
        flash(f"خطا در دریافت لیست کاربران: {str(e)}", "danger")
        users = []
    
    return render_template("admin_manage_users.html", users=users)

@app.route("/admin/delete_user/<student_id>")
def admin_delete_user(student_id):
    if not session.get("admin_logged_in"):
        flash("لطفاً به عنوان مدرس وارد شوید.", "warning")
        return redirect(url_for("admin_login"))
    
    try:
        with engine.begin() as conn:
            # حذف کاربر
            result = conn.execute(
                text("DELETE FROM stuid WHERE student_id = :student_id"),
                {"student_id": student_id}
            )
            
            if result.rowcount > 0:
                flash("کاربر با موفقیت حذف شد.", "success")
            else:
                flash("کاربر یافت نشد.", "warning")
                
    except Exception as e:
        flash(f"خطا در حذف کاربر: {str(e)}", "danger")
    
    return redirect(url_for("admin_manage_users"))



# مسیر مشاهده کوئری‌های ارسالی برای مدرس
@app.route("/admin/teacher_queries")
def admin_teacher_queries():
    if not session.get("admin_logged_in"):
        flash("لطفاً به عنوان ادمین وارد شوید.", "warning")
        return redirect(url_for("admin_login"))
    
    major = request.args.get("major", "")
    
    try:
        with engine.begin() as conn:
            # ایجاد جدول اگر وجود ندارد (با syntax مناسب PostgreSQL)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS teacher_queries (
                    id SERIAL PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    student_name TEXT NOT NULL,
                    major TEXT NOT NULL,
                    query TEXT NOT NULL,
                    output TEXT,
                    submission_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # گرفتن لیست کوئری‌های ارسالی با فیلتر
            query = text("""
                SELECT id, student_id, student_name, major, query, output, submission_time
                FROM teacher_queries
                WHERE 1=1
            """)
            
            params = {}
            
            if major:
                query = text(str(query) + " AND major = :major")
                params["major"] = major
            
            query = text(str(query) + " ORDER BY submission_time DESC")
            
            # تبدیل به لیست از دیکشنری‌ها
            rows = conn.execute(query, params).fetchall()
            
            # تبدیل به لیست از دیکشنری‌های قابل تغییر
            result_rows = []
            for row in rows:
                row_dict = {
                    "id": row[0],
                    "student_id": row[1],
                    "student_name": row[2],
                    "major": row[3],
                    "query": row[4],
                    "output": json.loads(row[5]) if row[5] else None,
                    "submission_time": row[6],
                    "submission_time_fa": format_datetime_fa(row[6]) if row[6] else "نامشخص"
                }
                result_rows.append(row_dict)
                    
    except Exception as e:
        flash(f"خطا در بارگذاری کوئری‌های ارسالی: {e}", "danger")
        result_rows = []
    
    return render_template("teacher_queries.html", 
                         queries=result_rows, 
                         majors=MAJORS,
                         selected_major=major)

@app.route("/send_to_teacher", methods=["GET"])
def send_to_teacher():
    if "student_id" not in session:
        flash("ابتدا وارد شوید.", "warning")
        return redirect(url_for("login"))
    
    # دریافت اطلاعات از session
    query = session.get("teacher_query", "")
    output_json = session.get("teacher_output", "")
    
    try:
        output = json.loads(output_json) if output_json else None
    except:
        output = None
    
    # ذخیره اطلاعات ارسال در دیتابیس
    try:
        with engine.begin() as conn:
            # ایجاد جدول اگر وجود ندارد
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS teacher_queries (
                    id SERIAL PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    student_name TEXT NOT NULL,
                    major TEXT NOT NULL,
                    query TEXT NOT NULL,
                    output TEXT,
                    submission_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # درج داده
            conn.execute(
                text("""
                    INSERT INTO teacher_queries 
                    (student_id, student_name, major, query, output)
                    VALUES (:student_id, :student_name, :major, :query, :output)
                """),
                {
                    "student_id": session["student_id"],
                    "student_name": session["name"],
                    "major": session["major"],
                    "query": query,
                    "output": output_json  # ذخیره به صورت JSON
                }
            )
        
        # پاک کردن اطلاعات از session
        session.pop("teacher_query", None)
        session.pop("teacher_output", None)
        
        flash('کوئری با موفقیت برای مدرس ارسال شد.', 'success')
    except Exception as e:
        app.logger.error(f"Error saving query: {e}")
        flash('خطا در ارسال کوئری برای مدرس.', 'danger')
    
    return redirect(url_for('run_test_query'))

@app.route("/test_date")
def test_date():
    """صفحه تست برای نمایش تبدیل تاریخ"""
    now_utc = datetime.utcnow()
    tehran_time = utc_to_tehran(now_utc)
    jalali_fa = gregorian_to_jalali_fa(tehran_time)
    formatted = format_datetime_fa(now_utc)
    
    return f"""
    UTC: {now_utc}<br>
    Tehran: {tehran_time}<br>
    Jalali FA: {jalali_fa}<br>
    Formatted: {formatted}<br><br>
    
    Test string conversion:<br>
    """
    
    # تست تبدیل رشته
    test_str = "2024-08-28 10:30:45"
    test_result = format_datetime_fa(test_str)
    return f"String '{test_str}' -> {test_result}"
