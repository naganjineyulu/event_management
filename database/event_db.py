import sqlite3
from pathlib import Path

database_path = Path(__file__).resolve().parent / "event.db"

conn = sqlite3.connect(database_path)
cursor = conn.cursor()

# Users table
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT,
    password TEXT,
    mobile TEXT,
    enrollment TEXT UNIQUE
)
""")

# Admin table
cursor.execute("""
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT
)
""")

# Events table
cursor.execute("""
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    description TEXT,
    date TEXT,
    location TEXT,
    category TEXT,
    price REAL
)
""")

# Registrations table
cursor.execute("""
CREATE TABLE IF NOT EXISTS registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    event_id INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(event_id) REFERENCES events(id)
)
""")

conn.commit()
conn.close()

print("Database and tables created successfully")
