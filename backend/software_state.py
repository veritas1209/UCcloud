"""Cache only a confirmed, non-secret installation result, not guesses."""
import json
from .database import db


def completed(team_id, server_id):
    with db() as conn:
        row = conn.execute('SELECT payload FROM instance_software_status WHERE team_id=? AND server_id=?',
                           (team_id, server_id)).fetchone()
    return json.loads(row['payload']) if row else None


def remember(team_id, server_id, message):
    if message.get('state') != 'ready':
        return
    safe = {key: message[key] for key in ('state', 'message', 'started_at', 'updated_at', 'step', 'total_steps') if key in message}
    with db() as conn:
        conn.execute('INSERT INTO instance_software_status VALUES(?,?,?) '
                     'ON CONFLICT(team_id,server_id) DO UPDATE SET payload=excluded.payload',
                     (team_id, server_id, json.dumps(safe, ensure_ascii=False)))


def forget(team_id, server_id):
    with db() as conn:
        conn.execute('DELETE FROM instance_software_status WHERE team_id=? AND server_id=?', (team_id, server_id))
