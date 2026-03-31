from flask import Flask, render_template, request, redirect, session, url_for
from pymongo import MongoClient
from bson.objectid import ObjectId
import random

app = Flask(__name__)
app.secret_key = "unievents_secret_key"

# ---------------- MONGODB CONNECTION ----------------
client = MongoClient("mongodb://localhost:27017/")
db = client["unievents"]

students = db["students"]
events_col = db["events"]
registrations = db["registrations"]

# ---------------- HOME ----------------
@app.route("/")
def home():
    return render_template("index.html")

# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]

        if not email.endswith("@paruluniversity.ac.in"):
            return "Only Parul University students allowed"

        students.insert_one({
            "name": request.form["name"],
            "mobile": request.form["mobile"],
            "email": email,
            "enrollment": request.form["enrollment"],
            "password": request.form["password"]
        })

        return redirect("/login")

    return render_template("register.html")

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        student = students.find_one({
            "enrollment": request.form["enrollment"],
            "password": request.form["password"]
        })

        if student:
            session["student_id"] = student["enrollment"]
            session["student_name"] = student["name"]
            return redirect("/student_dashboard")

        return "Invalid Credentials"

    return render_template("login.html")

# ---------------- DASHBOARD ----------------
@app.route("/student_dashboard")
def student_dashboard():
    if "student_id" not in session:
        return redirect("/login")

    total = registrations.count_documents({"student_id": session["student_id"]})

    return render_template(
        "student_dashboard.html",
        name=session["student_name"],
        total=total
    )

# ---------------- EVENTS ----------------
@app.route("/events")
def events():
    return render_template("events.html", events=events_col.find())

# ---------------- EVENT DETAILS ----------------
@app.route("/event/<id>")
def event_details(id):
    event = events_col.find_one({"_id": ObjectId(id)})
    return render_template("event_details.html", event=event)

# ---------------- REGISTER EVENT ----------------
@app.route("/register_event/<id>")
def register_event(id):
    event = events_col.find_one({"_id": ObjectId(id)})

    if event["price"] > 0:
        return redirect(f"/payment/{id}")

    return generate_ticket(id)

# ---------------- PAYMENT ----------------
@app.route("/payment/<id>", methods=["GET", "POST"])
def payment(id):
    event = events_col.find_one({"_id": ObjectId(id)})

    if request.method == "POST":
        return generate_ticket(id)

    return render_template("payment.html", event=event)

# ---------------- TICKET ----------------
def generate_ticket(event_id):
    ticket_id = "TICK" + str(random.randint(100000, 999999))

    registrations.insert_one({
        "student_id": session["student_id"],
        "event_id": event_id,
        "ticket_id": ticket_id
    })

    event = events_col.find_one({"_id": ObjectId(event_id)})

    return render_template("ticket.html", ticket_id=ticket_id, event=event)

# ================= ADMIN SECTION =================

@app.route("/admin_dashboard")
def admin_dashboard():
    return render_template("admin_dashboard.html", events=events_col.find())

@app.route("/add_event", methods=["GET", "POST"])
def add_event():
    if request.method == "POST":
        events_col.insert_one({
            "title": request.form["title"],
            "description": request.form["description"],
            "category": request.form["category"],
            "date": request.form["date"],
            "price": int(request.form["price"])
        })

        return redirect("/admin_dashboard")

    return render_template("add_event.html")

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)