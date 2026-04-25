from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session
import imghdr
import sqlite3
import os
import json
import random
import smtplib
import shutil
import string
from datetime import datetime
from functools import wraps
from email.message import EmailMessage
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.utils import secure_filename


# IMPORTANT: templates and static paths - use absolute paths for Vercel
api_dir = os.path.dirname(os.path.abspath(__file__))
base_dir = os.path.dirname(api_dir)


def load_local_env_file():
    env_file = os.path.join(base_dir, ".env.local")
    if not os.path.exists(env_file):
        return

    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        print(f"Unable to load .env.local: {e}")


load_local_env_file()

is_vercel = bool(os.environ.get("VERCEL"))
configured_data_dir = os.environ.get("EVENT_MANAGEMENT_DATA_DIR", "").strip()
runnable_data_dir = configured_data_dir or (
    os.path.join("/tmp", "event_management") if is_vercel else os.path.join(base_dir, "database")
)
app = Flask(
    __name__,
    template_folder=os.path.join(base_dir, "templates"),
    static_folder=os.path.join(base_dir, "static"),
)

# Configure session
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-change-this-in-production-2024")
app.config["SESSION_COOKIE_SECURE"] = is_vercel
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ================== ADMIN AUTHENTICATION ==================

BUNDLED_ADMIN_CREDENTIALS_FILE = os.path.join(base_dir, "database", "admin_credentials.json")
BUNDLED_DB_PATH = os.path.join(base_dir, "database", "event.db")
ALLOWED_PAYMENT_PROOF_EXTENSIONS = {"png", "jpg", "jpeg", "pdf", "webp"}
MAX_PAYMENT_PROOF_SIZE = 5 * 1024 * 1024

DEFAULT_ADMIN_CREDENTIALS = {}


def ensure_runtime_data_dir():
    os.makedirs(runnable_data_dir, exist_ok=True)


def get_admin_credentials_file():
    if not is_vercel and not configured_data_dir:
        return BUNDLED_ADMIN_CREDENTIALS_FILE

    ensure_runtime_data_dir()
    runtime_file = os.path.join(runnable_data_dir, "admin_credentials.json")
    if is_vercel and not os.path.exists(runtime_file) and os.path.exists(BUNDLED_ADMIN_CREDENTIALS_FILE):
        shutil.copy2(BUNDLED_ADMIN_CREDENTIALS_FILE, runtime_file)
    return runtime_file


def get_database_path():
    if not is_vercel and not configured_data_dir:
        return BUNDLED_DB_PATH

    ensure_runtime_data_dir()
    runtime_db = os.path.join(runnable_data_dir, "event.db")
    if not os.path.exists(runtime_db) and os.path.exists(BUNDLED_DB_PATH):
        shutil.copy2(BUNDLED_DB_PATH, runtime_db)
    return runtime_db


def get_payment_proofs_dir():
    ensure_runtime_data_dir()
    payment_proofs_dir = os.path.join(runnable_data_dir, "payment_proofs")
    os.makedirs(payment_proofs_dir, exist_ok=True)
    return payment_proofs_dir


def is_allowed_payment_proof(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_PAYMENT_PROOF_EXTENSIONS


def validate_payment_proof_file(uploaded_file):
    uploaded_file.stream.seek(0, os.SEEK_END)
    file_size = uploaded_file.stream.tell()
    uploaded_file.stream.seek(0)

    if file_size == 0:
        return False, "The payment proof file is empty."

    if file_size > MAX_PAYMENT_PROOF_SIZE:
        return False, "Payment proof must be smaller than 5 MB."

    original_name = uploaded_file.filename or ""
    extension = original_name.rsplit(".", 1)[1].lower()
    header = uploaded_file.stream.read(32)
    uploaded_file.stream.seek(0)

    if extension == "pdf":
        if not header.startswith(b"%PDF-"):
            return False, "The uploaded PDF proof is invalid."
        return True, None

    detected_type = imghdr.what(None, header)
    valid_image_types = {"png", "jpeg", "webp"}

    if detected_type not in valid_image_types:
        return False, "Upload a real screenshot image or PDF receipt for payment proof."

    if extension == "jpg":
        extension = "jpeg"

    if detected_type != extension:
        return False, "The payment proof file type does not match its extension."

    return True, None


def get_payment_proofs():
    return session.get("payment_proofs", {})


def get_payment_proof(event_id):
    return get_payment_proofs().get(str(event_id))


def save_payment_proof(event_id, uploaded_file):
    original_name = uploaded_file.filename or ""
    if not original_name.strip():
        return False, "Please upload your payment proof."

    if not is_allowed_payment_proof(original_name):
        return False, "Upload a PNG, JPG, JPEG, WEBP, or PDF payment proof."

    is_valid_file, validation_error = validate_payment_proof_file(uploaded_file)
    if not is_valid_file:
        return False, validation_error

    proof_filename = f"{event_id}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=12))}_{secure_filename(original_name)}"
    proof_path = os.path.join(get_payment_proofs_dir(), proof_filename)
    uploaded_file.save(proof_path)

    payment_proofs = get_payment_proofs()
    payment_proofs[str(event_id)] = {
        "original_name": original_name,
        "stored_name": proof_filename,
    }
    session["payment_proofs"] = payment_proofs
    session.modified = True
    return True, payment_proofs[str(event_id)]


def requires_payment_proof(event_id):
    if event_id <= 0:
        return False

    try:
        conn = sqlite3.connect(get_database_path())
        cursor = conn.cursor()
        cursor.execute("SELECT price FROM events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        conn.close()
        return bool(row) and float(row[0]) > 0
    except Exception as e:
        print(f"Error checking event price: {e}")
        return False


def load_admin_credentials():
    admin_credentials_file = get_admin_credentials_file()

    if os.path.exists(admin_credentials_file):
        try:
            with open(admin_credentials_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading admin credentials: {e}")
            return DEFAULT_ADMIN_CREDENTIALS.copy()

    save_admin_credentials(DEFAULT_ADMIN_CREDENTIALS.copy())
    return DEFAULT_ADMIN_CREDENTIALS.copy()


def save_admin_credentials(credentials):
    try:
        admin_credentials_file = get_admin_credentials_file()
        os.makedirs(os.path.dirname(admin_credentials_file), exist_ok=True)
        with open(admin_credentials_file, "w", encoding="utf-8") as f:
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


def get_reset_token_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt="password-reset")


def build_reset_token(scope, identity):
    serializer = get_reset_token_serializer()
    return serializer.dumps({"scope": scope, "identity": identity})


def read_reset_token(token, expected_scope, max_age=3600):
    serializer = get_reset_token_serializer()
    try:
        payload = serializer.loads(token, max_age=max_age)
    except SignatureExpired:
        return None, "This reset link has expired. Please request a new one."
    except BadSignature:
        return None, "This reset link is invalid. Please request a new one."

    if payload.get("scope") != expected_scope:
        return None, "This reset link is invalid. Please request a new one."

    return payload.get("identity"), None


def send_email_message(to_email, subject, body):
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from_email = os.environ.get("SMTP_FROM_EMAIL", smtp_username).strip()
    smtp_use_ssl = os.environ.get("SMTP_USE_SSL", "false").strip().lower() == "true"
    smtp_use_tls = os.environ.get("SMTP_USE_TLS", "true").strip().lower() != "false"

    if not smtp_host or not smtp_from_email:
        return False, "Email sending is not configured on the server yet."

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from_email
    message["To"] = to_email
    message.set_content(body)

    try:
        if smtp_use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
                if smtp_username:
                    server.login(smtp_username, smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                server.ehlo()
                if smtp_use_tls:
                    server.starttls()
                    server.ehlo()
                if smtp_username:
                    server.login(smtp_username, smtp_password)
                server.send_message(message)
        return True, None
    except Exception as e:
        print(f"Error sending email: {e}")
        return False, "Unable to send reset email right now. Please try again later."


def is_email_configured():
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_username = os.environ.get("SMTP_USERNAME", "").strip()
    smtp_from_email = os.environ.get("SMTP_FROM_EMAIL", smtp_username).strip()
    return bool(smtp_host and smtp_from_email)


def get_request_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return forwarded_for or request.remote_addr or "Unknown"


def send_login_notification(account_type, account_name, account_email):
    if not is_email_configured():
        print(f"Skipped {account_type.lower()} login email because SMTP is not configured.")
        return

    recipient_email = os.environ.get("LOGIN_ALERT_EMAIL", "").strip() or account_email
    if not recipient_email:
        print(f"Skipped {account_type.lower()} login email because no recipient email is available.")
        return

    login_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_agent = request.headers.get("User-Agent", "Unknown")
    subject = f"{account_type} Login Alert"
    body = (
        f"Hello {account_name},\n\n"
        f"A successful {account_type.lower()} login was detected.\n\n"
        f"Account: {account_name}\n"
        f"Email: {account_email or 'Not available'}\n"
        f"Time: {login_time}\n"
        f"IP address: {get_request_ip()}\n"
        f"Device/browser: {user_agent}\n\n"
        "If this was you, no action is needed. If you did not log in, reset your password immediately."
    )

    sent, error = send_email_message(recipient_email, subject, body)
    if not sent:
        print(f"Unable to send {account_type.lower()} login email: {error}")


def get_base_url():
    configured_url = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    if configured_url:
        return configured_url
    return request.url_root.rstrip("/")


def ensure_student_users_table():
    try:
        conn = sqlite3.connect(get_database_path())
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mobile TEXT,
                email TEXT NOT NULL,
                enrollment TEXT UNIQUE,
                password TEXT NOT NULL
            )
            """
        )

        cursor.execute("PRAGMA table_info(users)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if "mobile" not in existing_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN mobile TEXT")
        if "enrollment" not in existing_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN enrollment TEXT")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_enrollment ON users(enrollment)")

        cursor.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'")
        table_sql_row = cursor.fetchone()
        table_sql = (table_sql_row[0] or "").upper() if table_sql_row else ""
        needs_email_migration = "EMAIL TEXT UNIQUE" in table_sql

        if needs_email_migration:
            cursor.execute(
                """
                CREATE TABLE users_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    mobile TEXT,
                    email TEXT NOT NULL,
                    enrollment TEXT UNIQUE,
                    password TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO users_new (id, name, mobile, email, enrollment, password)
                SELECT id, name, mobile, email, enrollment, password
                FROM users
                """
            )
            cursor.execute("DROP TABLE users")
            cursor.execute("ALTER TABLE users_new RENAME TO users")

        cursor.execute("DROP INDEX IF EXISTS idx_users_email")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_enrollment ON users(enrollment)")

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error ensuring student users table: {e}")


def register_student(name, mobile, email, enrollment, password):
    ensure_student_users_table()

    try:
        conn = sqlite3.connect(get_database_path())
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE enrollment = ?", (enrollment,))
        existing_user = cursor.fetchone()
        if existing_user:
            conn.close()
            return False, "Student already registered with this enrollment number."

        cursor.execute(
            """
            INSERT INTO users (name, mobile, email, enrollment, password)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, mobile, email.lower(), enrollment, password),
        )
        conn.commit()
        conn.close()
        return True, "Student registered successfully."
    except Exception as e:
        print(f"Error registering student: {e}")
        return False, "Unable to register student right now."


def verify_student_credentials(username, enrollment, password):
    ensure_student_users_table()

    try:
        conn = sqlite3.connect(get_database_path())
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, email, enrollment
            FROM users
            WHERE enrollment = ? AND password = ?
            """,
            (enrollment, password),
        )
        user = cursor.fetchone()
        conn.close()

        if not user:
            return None

        submitted_username = username.strip().lower()
        registered_name = (user[1] or "").strip().lower()
        registered_email = (user[2] or "").strip().lower()

        if submitted_username not in {registered_name, registered_email}:
            return None

        return {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "enrollment": user[3],
        }
    except Exception as e:
        print(f"Error verifying student credentials: {e}")
        return None


def find_student_for_password_reset(enrollment, email):
    ensure_student_users_table()

    try:
        conn = sqlite3.connect(get_database_path())
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, email, enrollment
            FROM users
            WHERE enrollment = ? AND lower(email) = ?
            """,
            (enrollment, email.strip().lower()),
        )
        user = cursor.fetchone()
        conn.close()

        if not user:
            return None

        return {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "enrollment": user[3],
        }
    except Exception as e:
        print(f"Error finding student for password reset: {e}")
        return None


def update_student_password(user_id, new_password):
    ensure_student_users_table()

    try:
        conn = sqlite3.connect(get_database_path())
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, user_id))
        conn.commit()
        updated_rows = cursor.rowcount
        conn.close()
        return updated_rows > 0
    except Exception as e:
        print(f"Error updating student password: {e}")
        return False


def update_admin_password(username, email, new_password):
    credentials = load_admin_credentials()
    admin = credentials.get(username)

    if not admin or admin.get("email", "").strip().lower() != email.strip().lower():
        return False

    admin["password"] = new_password
    credentials[username] = admin
    return save_admin_credentials(credentials)


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
        conn = sqlite3.connect(get_database_path())
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
            credentials = load_admin_credentials()
            admin = credentials.get(username, {})
            session["admin_logged_in"] = True
            session["admin_username"] = username
            send_login_notification(
                "Admin",
                admin.get("full_name") or username,
                admin.get("email", ""),
            )
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


@app.route("/admin_test_email")
@login_required
def admin_test_email():
    credentials = load_admin_credentials()
    username = session.get("admin_username", "")
    admin = credentials.get(username, {})
    admin_email = admin.get("email", "").strip()

    if not admin_email:
        return "No email address is registered for this admin account.", 400

    sent, error = send_email_message(
        admin_email,
        "Event Management Email Test",
        (
            f"Hello {admin.get('full_name') or username},\n\n"
            "Your Event Management email configuration is working."
        ),
    )

    if not sent:
        return error, 500

    return f"Test email sent to {admin_email}."


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        enrollment = request.form.get("enrollment", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not enrollment or not password:
            return render_template("login.html", error="Name or shared email, enrollment number, and password are required.")

        student = verify_student_credentials(username, enrollment, password)
        if not student:
            return render_template(
                "login.html",
                error="Invalid student credentials. Use the same name or shared email, enrollment number, and password from registration.",
            )

        session["student_name"] = student["name"]
        session["student_id"] = student["enrollment"]
        session["student_email"] = student["email"]
        send_login_notification("Student", student["name"], student["email"])

        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        mobile = request.form.get("mobile", "").strip()
        email = request.form.get("email", "").strip().lower()
        enrollment = request.form.get("enrollment", "").strip()
        password = request.form.get("password", "").strip()

        if not all([name, mobile, email, enrollment, password]):
            return render_template("register.html", error="All registration fields are required.")
        if len(name) < 3:
            return render_template("register.html", error="Enter a valid full name.")
        if len(mobile) != 10 or not mobile.isdigit():
            return render_template("register.html", error="Enter a valid 10-digit mobile number.")
        if not email.endswith("@paruluniversity.ac.in"):
            return render_template("register.html", error="Use your university email address only.")
        if len(password) < 4:
            return render_template("register.html", error="Password must be at least 4 characters.")

        success, message = register_student(name, mobile, email, enrollment, password)
        if not success:
            return render_template("register.html", error=message)

        return render_template("login.html", success="Registration successful. Please log in with your registered details.")
    return render_template("register.html")


@app.route("/dashboard")
def dashboard():
    user_name = session.get("student_name", "User")
    return render_template("dashboard.html", user_name=user_name)


@app.route("/events")
def events():
    try:
        conn = sqlite3.connect(get_database_path())
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
        conn = sqlite3.connect(get_database_path())
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
    try:
        conn = sqlite3.connect(get_database_path())
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

    if request.method == "POST":
        if not event:
            return render_template("payment.html", event=None, error="Event not found.")

        uploaded_file = request.files.get("payment_proof")
        if not uploaded_file:
            return render_template(
                "payment.html",
                event=event,
                error="Upload your payment screenshot or receipt before downloading the ticket.",
            )

        success, result = save_payment_proof(event_id, uploaded_file)
        if not success:
            return render_template("payment.html", event=event, error=result)

        print(f"Payment proof uploaded successfully for event {event_id}.")
        return redirect(url_for("ticket", event_id=event_id))

    return render_template("payment.html", event=event)


@app.route("/ticket")
def ticket():
    event_id = request.args.get("event_id", 0, type=int)
    if requires_payment_proof(event_id) and not get_payment_proof(event_id):
        return redirect(url_for("payment_event", event_id=event_id))

    ticket_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    event = get_ticket_event(event_id)
    student = get_student_details()
    payment_proof = get_payment_proof(event_id)

    return render_template(
        "ticket.html",
        event=event,
        ticket_id=ticket_id,
        student_name=student["student_name"],
        student_id=student["student_id"],
        payment_proof=payment_proof,
    )


@app.route("/download_ticket/<ticket_id>")
def download_ticket(ticket_id):
    try:
        from datetime import datetime
        from io import BytesIO

        event_id = request.args.get("event_id", 0, type=int)
        if requires_payment_proof(event_id) and not get_payment_proof(event_id):
            return redirect(url_for("payment_event", event_id=event_id))

        event = get_ticket_event(event_id)
        student = get_student_details()
        payment_proof = get_payment_proof(event_id)

        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas

            pdf_buffer = BytesIO()
            c = canvas.Canvas(pdf_buffer, pagesize=letter)
            width, height = letter

            purple = colors.HexColor("#5f3dc4")
            purple_dark = colors.HexColor("#43208c")
            purple_soft = colors.HexColor("#efe9ff")
            gold = colors.HexColor("#facc15")
            green = colors.HexColor("#16a34a")
            slate = colors.HexColor("#334155")
            light_border = colors.HexColor("#d8ccff")

            # Page background card
            c.setFillColor(colors.whitesmoke)
            c.rect(0, 0, width, height, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.roundRect(28, 34, width - 56, height - 68, 18, stroke=0, fill=1)

            # Header band
            c.setFillColor(purple)
            c.roundRect(40, height - 145, width - 80, 95, 16, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 28)
            c.drawString(60, height - 88, "EVENT TICKET")
            c.setFont("Helvetica", 11)
            c.drawString(60, height - 108, "University Event Management System")

            # Ticket id pill
            c.setFillColor(gold)
            c.roundRect(40, height - 192, 220, 34, 12, stroke=0, fill=1)
            c.setFillColor(purple_dark)
            c.setFont("Helvetica-Bold", 16)
            c.drawString(54, height - 179, f"Ticket ID: {ticket_id}")

            # Event title block
            c.setFillColor(purple_soft)
            c.roundRect(40, height - 270, width - 80, 62, 14, stroke=0, fill=1)
            c.setFillColor(purple_dark)
            c.setFont("Helvetica-Bold", 18)
            c.drawString(56, height - 232, event["title"][:42])
            c.setFont("Helvetica", 10)
            c.setFillColor(slate)
            c.drawString(56, height - 249, event["description"][:82])

            # Student section
            c.setFillColor(colors.white)
            c.setStrokeColor(light_border)
            c.roundRect(40, height - 380, width - 80, 86, 14, stroke=1, fill=1)
            c.setFillColor(purple)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(56, height - 315, "STUDENT INFORMATION")
            c.setFillColor(slate)
            c.setFont("Helvetica", 11)
            c.drawString(56, height - 338, f"Name: {student['student_name']}")
            c.drawString(56, height - 358, f"Enrollment ID: {student['student_id']}")

            # Event details section
            c.roundRect(40, height - 505, width - 80, 108, 14, stroke=1, fill=1)
            c.setFillColor(purple)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(56, height - 420, "EVENT DETAILS")
            c.setFillColor(slate)
            c.setFont("Helvetica", 11)
            c.drawString(56, height - 443, f"Event: {event['title'][:55]}")
            c.drawString(56, height - 463, f"Description: {event['description'][:68]}")
            c.setFillColor(green)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(56, height - 485, "Status: Confirmed Registration")

            if payment_proof:
                c.setFillColor(slate)
                c.setFont("Helvetica", 10)
                c.drawString(56, height - 500, f"Payment Proof: {payment_proof['original_name'][:48]}")

            # Instructions section
            c.setFillColor(purple_soft)
            c.setStrokeColor(colors.white)
            c.roundRect(40, height - 620, width - 80, 94, 14, stroke=0, fill=1)
            c.setFillColor(purple_dark)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(56, height - 545, "INSTRUCTIONS")
            c.setFillColor(slate)
            c.setFont("Helvetica", 10)
            c.drawString(56, height - 566, "- Present this ticket at the event entrance")
            c.drawString(56, height - 584, "- Keep this ticket in a safe place")
            c.drawString(56, height - 602, "- This serves as your proof of registration")

            # Footer
            c.setStrokeColor(light_border)
            c.line(40, 92, width - 40, 92)
            c.setFillColor(colors.grey)
            c.setFont("Helvetica", 9)
            c.drawString(40, 72, f"Generated: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}")
            c.setFillColor(purple)
            c.setFont("Helvetica-Bold", 11)
            c.drawRightString(width - 40, 72, "Thank you for registering!")

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
Payment Proof: {payment_proof['original_name'] if payment_proof else 'Not required'}

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
        conn = sqlite3.connect(get_database_path())
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
        conn = sqlite3.connect(get_database_path())
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
        conn = sqlite3.connect(get_database_path())
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
                conn = sqlite3.connect(get_database_path())
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
        conn = sqlite3.connect(get_database_path())
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
        enrollment = request.form.get("enrollment", "").strip()
        email = request.form.get("email", "").strip().lower()

        if not enrollment or not email:
            return render_template("forgot_password.html", error="Please provide both enrollment number and email!")

        student = find_student_for_password_reset(enrollment, email)
        if not student:
            return render_template("forgot_password.html", error="Enrollment number and email do not match any student account.")

        token = build_reset_token("student", {"user_id": student["id"], "email": student["email"]})
        reset_link = f"{get_base_url()}{url_for('reset_password', token=token)}"
        if not is_email_configured():
            return render_template(
                "forgot_password.html",
                success="Email sending is not configured yet. Use this reset link to continue.",
                reset_link=reset_link,
            )

        sent, error = send_email_message(
            student["email"],
            "Student Password Reset",
            f"Hello {student['name']},\n\nUse this link to reset your password:\n{reset_link}\n\nThis link expires in 1 hour.",
        )
        if not sent:
            return render_template("forgot_password.html", error=error)

        return render_template(
            "forgot_password.html",
            success="Password reset link has been sent to your email!",
        )

    return render_template("forgot_password.html")


@app.route("/admin_forgot_password", methods=["GET", "POST"])
def admin_forgot_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()

        if not username or not email:
            return render_template("admin_forgot_password.html", error="Please provide both username and email!")

        credentials = load_admin_credentials()
        if username in credentials and credentials[username].get("email", "").strip().lower() == email:
            token = build_reset_token("admin", {"username": username, "email": email})
            reset_link = f"{get_base_url()}{url_for('admin_reset_password', token=token)}"
            if not is_email_configured():
                return render_template(
                    "admin_forgot_password.html",
                    success="Email sending is not configured yet. Use this reset link to continue.",
                    reset_link=reset_link,
                )

            sent, error = send_email_message(
                email,
                "Admin Password Reset",
                f"Hello {credentials[username].get('full_name', username)},\n\nUse this link to reset your admin password:\n{reset_link}\n\nThis link expires in 1 hour.",
            )
            if not sent:
                return render_template("admin_forgot_password.html", error=error)

            return render_template(
                "admin_forgot_password.html",
                success="Password reset link has been sent to your email!",
            )

        return render_template("admin_forgot_password.html", error="Username and email do not match!")

    return render_template("admin_forgot_password.html")


@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    identity, token_error = read_reset_token(token, "student")
    if token_error:
        return render_template("reset_password.html", mode="student", error=token_error)

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if len(password) < 4:
            return render_template("reset_password.html", mode="student", error="Password must be at least 4 characters.")
        if password != confirm_password:
            return render_template("reset_password.html", mode="student", error="Passwords do not match.")
        if not update_student_password(identity["user_id"], password):
            return render_template("reset_password.html", mode="student", error="Unable to reset password right now.")

        return render_template("reset_password.html", mode="student", success="Password updated successfully. You can now log in.")

    return render_template("reset_password.html", mode="student")


@app.route("/admin_reset_password/<token>", methods=["GET", "POST"])
def admin_reset_password(token):
    identity, token_error = read_reset_token(token, "admin")
    if token_error:
        return render_template("reset_password.html", mode="admin", error=token_error)

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if len(password) < 6:
            return render_template("reset_password.html", mode="admin", error="Password must be at least 6 characters.")
        if password != confirm_password:
            return render_template("reset_password.html", mode="admin", error="Passwords do not match.")
        if not update_admin_password(identity["username"], identity["email"], password):
            return render_template("reset_password.html", mode="admin", error="Unable to reset admin password right now.")

        return render_template("reset_password.html", mode="admin", success="Admin password updated successfully. You can now log in.")

    return render_template("reset_password.html", mode="admin")


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
