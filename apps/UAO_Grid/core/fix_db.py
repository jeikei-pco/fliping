import sys

with open('apps/UAO_Grid/core/database.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Remove lines 1 to 144 (which are the duplicated top half)
cleaned = "".join(lines[144:])

# Fix the triple quote issue that caused SyntaxError
old_sql = """                conn.execute('''
                    INSERT INTO grid_status_cache (id, payload_json, updated_at)
                    VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        updated_at   = excluded.updated_at
                ''', (json.dumps(payload), datetime.utcnow().isoformat()))"""

new_sql = """                conn.execute(
                    'INSERT INTO grid_status_cache (id, payload_json, updated_at) VALUES (1, ?, ?)'
                    ' ON CONFLICT(id) DO UPDATE SET'
                    ' payload_json = excluded.payload_json,'
                    ' updated_at = excluded.updated_at',
                    (json.dumps(payload), datetime.utcnow().isoformat())
                )"""

cleaned = cleaned.replace(old_sql, new_sql)

with open('apps/UAO_Grid/core/database.py', 'w', encoding='utf-8') as f:
    f.write(cleaned)

print("File fixed successfully!")
