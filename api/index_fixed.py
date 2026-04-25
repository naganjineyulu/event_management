from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session
import sqlite3
import os
import json
import random
import string
from functools import wraps


# IMPORTANT: templates and static paths - use absolute paths for Vercel
api_dir = os.path.dirname(os.path.abspath(__file__))
base_dir = os.path.dirname(api_dir)
app = Flask(
    __name__,
    template_folder=os.path.join(base_dir, "templates"),
    static_folder=os.path.join(base_dir, "static"),
)

# Configure session
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-change-this-in-production-2024")
app.config["SESSION_COOKIE_SECURE"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ================== ADMIN AUTHENTICATION ==================

ADMIN_CREDENTIALS_FILE = os.path.join(base_dir, "database", "admin_credentials.json")

DEFAULT_ADMIN_CREDENTIALS = {}


def load_admin_credentials():
    if os.path.exists(ADMIN_CREDENTIALS_FILE):
        try:
            with open(ADMIN_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading admin credentials: {e}")
            return DEFAULT_ADMIN_CREDENTIALS.copy()

    save_admin_credentials(DEFAULT_ADMIN_CREDENTIALS.copy())
    return DEFAULT_ADMIN_CREDENTIALS.copy()


def save_admin_credentials(credentials):
    try:
        os.makedirs(os.path.dirname(ADMIN_CREDENTIALS_FILE), exist_ok=True)
        with open(ADMIN_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(credentials, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving admin credentials: {e}")
        return False


def admin_exists(username):
    credentials = load_admin_credentials()
    return username in credentials


def register_new_admin(full_name, email, username, password):
    if admin_exists(username):
        return False, "Username already exists"

    credentials = load_admin_credentials()
    credentials[username] = {
        "password": password,
        "email": email,
        "full_name": full_name,
    }

    if save_admin_credentials(credentials):
        return True, "Admin registered successfully"
    return False, "Failed to save admin credentials"


def verify_admin_credentials(username, password):
    credentials = load_admin_credentials()
    return username in credentials and credentials[username]["password"] == password


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "admin_logged_in" not in session:
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)

    return decorated_function


def get_student_details():
    return {
        "student_name": request.args.get("student_name", "").strip() or session.get("student_name", "Student"),
        "student_id": request.args.get("student_id", "").strip() or session.get("student_id", "00000"),
    }


def get_ticket_event(event_id):
    event = {
        "id": event_id if event_id > 0 else 0,
        "title": request.args.get("event_title", "").strip() or "Event Ticket",
        "description": request.args.get("event_description", "").strip()
        or "Thank you for registering for the event!",
    }

    if event_id <= 0:
        return event

    try:
        db_path = os.path.join(base_dir, "database", "event.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, description FROM events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            event = {"id": row[0], "title": row[1], "description": row[2]}
    except Exception as e:
        print(f"Error fetching event for ticket: {e}")

    return event


@app.route("/admin_register", methods=["GET", "POST"])
def admin_register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not all([full_name, email, username, password, confirm_password]):
            return render_template("admin_register.html", error="All fields are required!")
        if len(username) < 3:
            return render_template("admin_register.html", error="Username must be at least 3 characters!")
        if len(password) < 6:
            return render_template("admin_register.html", error="Password must be at least 6 characters!")
        if password != confirm_password:
            return render_template("admin_register.html", error="Passwords do not match!")
        if admin_exists(username):
            return render_template("admin_register.html", error="Username already exists! Choose a different one.")

        success, message = register_new_admin(full_name, email, username, password)
        if success:
            print(f"New admin '{username}' registered successfully.")
            return redirect(url_for("admin_login"))

        print(f"Failed to register admin: {message}")
        return render_template("admin_register.html", error=message)

    return render_template("admin_register.html")


@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if verify_admin_credentials(username, password):
            session["admin_logged_in"] = True
            session["admin_username"] = username
            print(f"Admin '{username}' logged in successfully.")
            return redirect(url_for("admin_dashboard"))

        print(f"Failed admin login attempt with username: {username}")
        return render_template("admin_login.html", error="Invalid username or password!")

    return render_template("admin_login.html")


@app.route("/admin_logout")
def admin_logout():
    if "admin_username" in session:
        username = session["admin_username"]
        session.clear()
        print(f"Admin '{username}' logged out.")
    return redirect(url_for("home"))


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        enrollment = request.form.get("enrollment", "").strip()

        session["student_name"] = username or "Student"
        session["student_id"] = enrollment or "00000"

        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/dashboard")
def dashboard():
    user_name = session.get("student_name", "User")
    return render_template("dashboard.html", user_name=user_name)


@app.route("/events")
def events():
    try:
        db_path = os.path.join(base_dir, "database", "event.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, description, category, price FROM events")
        rows = cursor.fetchall()
        conn.close()

        events_list = []
        for row in rows:
            events_list.append(
                {
                    "id": row[0],
                    "title": row[1],
                    "description": row[2],
                    "category": row[3],
                    "price": row[4],
                }
            )
    except Exception as e:
        print(f"Error fetching events: {e}")
        events_list = []

    return render_template("events.html", events=events_list)


@app.route("/event/<int:event_id>")
def event_details(event_id):
    try:
        db_path = os.path.join(base_dir, "database", "event.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, description, category, price FROM events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            event = {
                "id": row[0],
                "title": row[1],
                "description": row[2],
                "category": row[3],
                "price": row[4],
            }
        else:
            event = None
    except Exception as e:
        print(f"Error fetching event: {e}")
        event = None

    return render_template("event_details.html", event=event)


@app.route("/payment", methods=["GET", "POST"])
def payment():
    if request.method == "POST":
        print("Payment completed successfully.")
        return redirect(url_for("ticket", event_id=0))
    return render_template("payment.html")


@app.route("/payment/<int:event_id>", methods=["GET", "POST"])
def payment_event(event_id):
    if request.method == "POST":
        print(f"Payment completed successfully for event {event_id}.")
        return redirect(url_for("ticket", event_id=event_id))

    try:
        db_path = os.path.join(base_dir, "database", "event.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, description, category, price FROM events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            event = {
                "id": row[0],
                "title": row[1],
                "description": row[2],
                "category": row[3],
                "price": row[4],
            }
        else:
            event = None
    except Exception as e:
        print(f"Error fetching event: {e}")
        event = None

    return render_template("payment.html", event=event)


@app.route("/ticket")
def ticket():
    event_id = request.args.get("event_id", 0, type=int)
    ticket_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    event = get_ticket_event(event_id)
    student = get_student_details()

    return render_template(
        "ticket.html",
        event=event,
        ticket_id=ticket_id,
        student_name=student["student_name"],
        student_id=student["student_id"],
    )


@app.route("/download_ticket/<ticket_id>")
def download_ticket(ticket_id):
    try:
        from datetime import datetime
        from io import BytesIO

        event_id = request.args.get("event_id", 0, type=int)
        event = get_ticket_event(event_id)
        student = get_student_details()

        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas

            pdf_buffer = BytesIO()
            c = canvas.Canvas(pdf_buffer, pagesize=letter)
            width, height = letter

            c.setFont("Helvetica-Bold", 24)
            c.drawString(50, height - 50, "EVENT TICKET")

            c.setFont("Helvetica", 10)
            c.drawString(50, height - 70, "University Event Management System")

            c.line(50, height - 80, width - 50, height - 80)

            c.setFont("Helvetica-Bold", 16)
            c.drawString(50, height - 110, f"Ticket ID: {ticket_id}")

            c.setFont("Helvetica", 12)
            c.drawString(50, height - 140, "STUDENT INFORMATION")
            c.setFont("Helvetica", 11)
            c.drawString(60, height - 160, f"Name: {student['student_name']}")
            c.drawString(60, height - 180, f"Enrollment ID: {student['student_id']}")

            c.setFont("Helvetica", 12)
            c.drawString(50, height - 220, "EVENT DETAILS")
            c.setFont("Helvetica", 11)
            c.drawString(60, height - 240, f"Event: {event['title']}")
            c.drawString(60, height - 260, f"Description: {event['description'][:60]}")
            c.drawString(60, height - 280, "Status: Confirmed Registration")

            c.setFont("Helvetica", 12)
            c.drawString(50, height - 300, "INSTRUCTIONS")
            c.setFont("Helvetica", 10)
            c.drawString(60, height - 320, "- Present this ticket at the event entrance")
            c.drawString(60, height - 335, "- Keep this ticket in a safe place")
            c.drawString(60, height - 350, "- This serves as your proof of registration")

            c.setFont("Helvetica", 9)
            c.drawString(50, height - 390, f"Generated: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}")

            c.line(50, height - 410, width - 50, height - 410)
            c.setFont("Helvetica", 10)
            c.drawString(50, height - 430, "Thank you for registering!")

            c.save()
            pdf_buffer.seek(0)

            response = app.response_class(
                response=pdf_buffer.getvalue(),
                status=200,
                mimetype="application/pdf",
                headers={"Content-Disposition": f"attachment;filename=ticket_{ticket_id}.pdf"},
            )
            return response
        except ImportError:
            print("reportlab not installed, using text format")
            raise Exception("PDF library not available")

    except Exception as e:
        print(f"Error creating PDF: {e}")
        try:
            from datetime import datetime

            event_id = request.args.get("event_id", 0, type=int)
            event = get_ticket_event(event_id)
            student = get_student_details()
            ticket_content = f"""UNIVERSITY EVENT MANAGEMENT SYSTEM
EVENT TICKET

Ticket ID: {ticket_id}

STUDENT INFORMATION:
Name: {student['student_name']}
Enrollment ID: {student['student_id']}

EVENT DETAILS:
Event: {event['title']}
Description: {event['description']}
Status: Confirmed Registration

INSTRUCTIONS:
- Present this ticket at the event entrance
- Keep this ticket in a safe place
- This serves as your proof of registration

Generated: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}

Thank you for registering!
"""
            response = app.response_class(
                response=ticket_content,
                status=200,
                mimetype="text/plain",
                headers={"Content-Disposition": f"attachment;filename=ticket_{ticket_id}.txt"},
            )
            return response
        except Exception:
            return redirect(url_for("ticket"))


@app.route("/register_event/<int:event_id>")
def register_event(event_id):
    try:
        db_path = os.path.join(base_dir, "database", "event.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, price FROM events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            selected_event_id = row[0]
            price = row[2]

            if price == 0:
                print(f"Registered for free event: {row[1]}")
                return redirect(url_for("ticket", event_id=selected_event_id))

            return redirect(url_for("payment_event", event_id=selected_event_id))

        return redirect(url_for("events"))
    except Exception as e:
        print(f"Error registering for event: {e}")
        return redirect(url_for("events"))


@app.route("/logout")
def logout():
    session.pop("student_name", None)
    session.pop("student_id", None)
    return redirect(url_for("home"))


@app.route("/admin_dashboard")
@login_required
def admin_dashboard():
    try:
        db_path = os.path.join(base_dir, "database", "event.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, description, date, location, category, price FROM events")
        rows = cursor.fetchall()
        conn.close()

        events = []
        for row in rows:
            events.append(
                {
                    "id": row[0],
                    "title": row[1],
                    "description": row[2],
                    "date": row[3],
                    "location": row[4],
                    "category": row[5],
                    "price": row[6],
                }
            )
    except Exception as e:
        print(f"Error fetching events: {e}")
        events = []

    return render_template("admin_dashboard.html", events=events)


@app.route("/delete_event/<int:event_id>")
@login_required
def delete_event(event_id):
    try:
        db_path = os.path.join(base_dir, "database", "event.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT title FROM events WHERE id = ?", (event_id,))
        event = cursor.fetchone()

        if event:
            event_title = event[0]
            cursor.execute("DELETE FROM events WHERE id = ?", (event_id,))
            conn.commit()
            conn.close()
            print(f"Event '{event_title}' deleted successfully.")
            return redirect(url_for("admin_dashboard"))

        print(f"Event with ID {event_id} not found")
        conn.close()
        return redirect(url_for("admin_dashboard"))
    except Exception as e:
        print(f"Error deleting event: {e}")
        return redirect(url_for("admin_dashboard"))


@app.route("/add_event", methods=["GET", "POST"])
@login_required
def add_event():
    if request.method == "POST":
        title = request.form.get("title")
        description = request.form.get("description")
        category = request.form.get("category")
        date = request.form.get("date")
        location = request.form.get("location", category)
        price = request.form.get("price")

        if title and description and category and date and price:
            try:
                db_path = os.path.join(base_dir, "database", "event.db")
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO events (title, description, date, location, category, price)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (title, description, date, location, category, price),
                )
                conn.commit()
                conn.close()
                print(f"Event '{title}' added successfully.")
                return redirect(url_for("admin_dashboard"))
            except Exception as e:
                print(f"Error adding event: {e}")
                return redirect(url_for("add_event"))

        print("Missing form data")
        return redirect(url_for("add_event"))

    return render_template("add_event.html")


@app.route("/student_dashboard")
def student_dashboard():
    try:
        db_path = os.path.join(base_dir, "database", "event.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM events")
        total_events = cursor.fetchone()[0]
        conn.close()
    except Exception as e:
        print(f"Error fetching stats: {e}")
        total_events = 0

    return render_template("student_dashboard.html", name=session.get("student_name", "Student"), total=total_events)


@app.route("/account")
def account():
    user_name = session.get("student_name", "User")
    return render_template("dashboard.html", user_name=user_name)


@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()

        if not username or not email:
            return render_template("forgot_password.html", error="Please provide both username and email!")

        print(f"Password reset link sent to {email}")
        return render_template(
            "forgot_password.html",
            success="Password reset link has been sent to your email!",
        )

    return render_template("forgot_password.html")


@app.route("/admin_forgot_password", methods=["GET", "POST"])
def admin_forgot_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()

        if not username or not email:
            return render_template("admin_forgot_password.html", error="Please provide both username and email!")

        credentials = load_admin_credentials()
        if username in credentials and credentials[username].get("email") == email:
            print(f"Admin password reset link sent to {email}")
            return render_template(
                "admin_forgot_password.html",
                success="Password reset link has been sent to your email!",
            )

        return render_template("admin_forgot_password.html", error="Username and email do not match!")

    return render_template("admin_forgot_password.html")


@app.route("/sitemap.xml")
def sitemap():
    return send_from_directory(os.path.join(base_dir, "static"), "sitemap.xml", mimetype="application/xml")


@app.route("/robots.txt")
def robots():
    return send_from_directory(os.path.join(base_dir, "static"), "robots.txt", mimetype="text/plain")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(os.path.join(base_dir, "static"), "manifest.json", mimetype="application/json")


@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(os.path.join(base_dir, "static"), "service-worker.js", mimetype="application/javascript")


@app.errorhandler(404)
def not_found(e):
    return "404 Not Found", 404


handler = app


if __name__ == "__main__":
    app.run(debug=True, port=5000)
