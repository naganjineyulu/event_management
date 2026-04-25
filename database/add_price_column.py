import sqlite3
from pathlib import Path

database_path = Path(__file__).resolve().parent / "event.db"

conn = sqlite3.connect(database_path)
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(events)")
columns = {row[1] for row in cursor.fetchall()}

if "price" not in columns:
    cursor.execute("ALTER TABLE events ADD COLUMN price INTEGER DEFAULT 0")
    conn.commit()
    print("Price column added successfully")
else:
    print("Price column already exists")

conn.close()
