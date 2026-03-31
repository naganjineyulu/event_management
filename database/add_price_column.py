import sqlite3

conn = sqlite3.connect("database/event.db")
conn.execute("ALTER TABLE events ADD COLUMN price INTEGER DEFAULT 0")
conn.commit()
conn.close()

print("Price column added successfully")
