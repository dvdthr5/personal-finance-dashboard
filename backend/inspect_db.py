import sqlite3

# Open your database
conn = sqlite3.connect("portfolio.db")
cur = conn.cursor()

# Show all rows in holdings
cur.execute("SELECT * FROM holdings;")
rows = cur.fetchall()

print("Current rows in holdings:")
for row in rows:
    print(row)

conn.close()
