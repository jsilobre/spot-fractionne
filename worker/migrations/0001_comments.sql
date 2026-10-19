-- Comments left by visitors on a spot (ADR 0019). No account, no IP address:
-- a pseudonym, the text and the date.
CREATE TABLE comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  spot_id TEXT NOT NULL,
  author TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  -- visible: shown; hidden: hidden by reports or by the moderator;
  -- approved: checked by the moderator, reports no longer hide it.
  status TEXT NOT NULL DEFAULT 'visible' CHECK (status IN ('visible', 'hidden', 'approved')),
  reports INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX comments_spot ON comments (spot_id, created_at);
