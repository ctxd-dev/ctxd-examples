"""Persistent admission limits and a small cache for curated public questions."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    visitor_window: int = 5
    visitor_day: int = 20
    rpm: int = 3
    rpd: int = 50
    tpm: int = 60000


class Limited(Exception):
    def __init__(self, message, retry_after):
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(message)


class Budget:
    def __init__(self, database, policy=Policy(), clock=time.time):
        self.database, self.policy, self.clock = database, policy, clock
        database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS visitors (identity TEXT, at REAL);
                CREATE INDEX IF NOT EXISTS visitor_time ON visitors(identity, at);
                CREATE TABLE IF NOT EXISTS calls (at REAL, tokens INTEGER);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, at REAL);
            ''')
            db.execute("INSERT OR IGNORE INTO settings VALUES ('salt', ?)", (secrets.token_hex(32),))
            self.salt = db.execute("SELECT value FROM settings WHERE key='salt'").fetchone()[0].encode()

    def connect(self):
        return sqlite3.connect(self.database, timeout=5)

    def visitor(self, address):
        identity = hmac.new(self.salt, address.encode(), hashlib.sha256).hexdigest()
        now, p = self.clock(), self.policy
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM visitors WHERE at <= ?', (now - 86400,))
            times = [r[0] for r in db.execute('SELECT at FROM visitors WHERE identity=? ORDER BY at', (identity,))]
            for duration, maximum in [(600, p.visitor_window), (86400, p.visitor_day)]:
                recent = [t for t in times if t > now - duration]
                if len(recent) >= maximum:
                    raise Limited('This network has reached its question allowance. Browse the docs or try again later.', recent[0] + duration - now)
            db.execute('INSERT INTO visitors VALUES (?,?)', (identity, now))

    def reserve(self, tokens):
        now, p = self.clock(), self.policy
        lease = secrets.token_hex(16)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            settings = dict(db.execute('SELECT key, value FROM settings'))
            until = float(settings.get('cooldown', '0'))
            if until > now:
                raise Limited('The model service is cooling down. Documentation remains available.', until - now)
            if float(settings.get('lease_until', '0')) > now:
                raise Limited('Another answer is being generated. Try again shortly.', 10)
            db.execute('DELETE FROM calls WHERE at <= ?', (now - 86400,))
            calls = list(db.execute('SELECT at, tokens FROM calls ORDER BY at'))
            for duration, maximum in [(60, p.rpm), (86400, p.rpd)]:
                recent = [row for row in calls if row[0] > now - duration]
                if len(recent) >= maximum:
                    raise Limited('The shared assistant allowance is used up. Documentation remains available.', recent[0][0] + duration - now)
            recent = [row for row in calls if row[0] > now - 60]
            if tokens > p.tpm:
                raise Limited('This question needs more context than the configured token allowance permits.', 60)
            if sum(row[1] for row in recent) + tokens > p.tpm:
                raise Limited('The shared token allowance is busy. Try again shortly.', recent[0][0] + 60 - now)
            db.execute('INSERT INTO calls VALUES (?,?)', (now, tokens))
            db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', [('lease', lease), ('lease_until', str(now + 180))])
        return lease

    def release(self, lease):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM settings WHERE key='lease'").fetchone()
            if row and row[0] == lease:
                db.execute("DELETE FROM settings WHERE key IN ('lease','lease_until')")

    def cooldown(self, seconds=60):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('cooldown', ?)", (str(self.clock() + seconds),))

    def cached(self, key):
        with self.connect() as db:
            row = db.execute('SELECT value FROM cache WHERE key=? AND at>?', (key, self.clock() - 86400)).fetchone()
        return json.loads(row[0]) if row else None

    def cache(self, key, result):
        with self.connect() as db:
            db.execute('DELETE FROM cache WHERE at<=?', (self.clock() - 86400,))
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, json.dumps(result), self.clock()))
            db.execute('DELETE FROM cache WHERE key NOT IN (SELECT key FROM cache ORDER BY at DESC LIMIT 32)')
