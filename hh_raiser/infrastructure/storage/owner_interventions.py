from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class PendingCaptcha:
    """Owner-visible metadata for one CAPTCHA without browser/session secrets."""

    challenge_id: str
    screenshot_path: Path
    prompt: str
    created_at: datetime


class OwnerInterventionStore:
    """Small cross-process mailbox shared by Playwright and the Telegram bot."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir.resolve()
        self.database_path = self.state_dir / "owner-interventions.sqlite3"
        self.screenshot_dir = self.state_dir / "owner-interventions"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._restrict(self.screenshot_dir, 0o700)
        self._initialize()

    def create_captcha(self, challenge_id: str, screenshot_path: Path, prompt: str) -> None:
        """Publish a pending CAPTCHA after its cropped screenshot has been written."""

        relative_path = screenshot_path.resolve().relative_to(self.screenshot_dir)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO owner_interventions (
                    challenge_id, kind, status, screenshot_name, prompt, created_at
                ) VALUES (?, 'captcha_text', 'pending', ?, ?, ?)
                """,
                (challenge_id, str(relative_path), prompt, datetime.now(UTC).isoformat()),
            )

    def pending_for_user(self, user_id: int) -> list[PendingCaptcha]:
        """Return pending challenges that have not yet been delivered to this owner."""

        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT i.challenge_id, i.screenshot_name, i.prompt, i.created_at
                FROM owner_interventions AS i
                LEFT JOIN owner_intervention_notifications AS n
                  ON n.challenge_id = i.challenge_id AND n.user_id = ?
                WHERE i.status = 'pending' AND n.challenge_id IS NULL
                ORDER BY i.created_at
                """,
                (user_id,),
            ).fetchall()
        return [
            PendingCaptcha(
                challenge_id=str(row[0]),
                screenshot_path=self._screenshot_path(str(row[1])),
                prompt=str(row[2]),
                created_at=datetime.fromisoformat(str(row[3])),
            )
            for row in rows
        ]

    def mark_notified(self, challenge_id: str, user_id: int, message_id: int) -> None:
        """Remember the exact Telegram message that must be replied to."""

        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO owner_intervention_notifications (
                    challenge_id, user_id, message_id, sent_at
                ) VALUES (?, ?, ?, ?)
                """,
                (challenge_id, user_id, message_id, datetime.now(UTC).isoformat()),
            )

    def submit_reply(self, user_id: int, message_id: int, answer: str) -> bool:
        """Attach an owner's answer only to its still-pending one-time challenge."""

        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE owner_interventions
                SET status = 'answered', answer = ?, answered_at = ?
                WHERE challenge_id = (
                    SELECT challenge_id
                    FROM owner_intervention_notifications
                    WHERE user_id = ? AND message_id = ?
                ) AND status = 'pending' AND answer IS NULL
                """,
                (answer, datetime.now(UTC).isoformat(), user_id, message_id),
            )
        return cursor.rowcount == 1

    def take_answer(self, challenge_id: str) -> str | None:
        """Consume an answer once and erase it from persistent storage immediately."""

        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT answer FROM owner_interventions
                WHERE challenge_id = ? AND status = 'answered'
                """,
                (challenge_id,),
            ).fetchone()
            if row is None or row[0] is None:
                connection.commit()
                return None
            answer = str(row[0])
            connection.execute(
                """
                UPDATE owner_interventions
                SET status = 'processing', answer = NULL, answer_consumed_at = ?
                WHERE challenge_id = ?
                """,
                (datetime.now(UTC).isoformat(), challenge_id),
            )
            connection.commit()
            return answer

    def finish(self, challenge_id: str, status: str) -> None:
        """Close a challenge and remove its sensitive screenshot from local storage."""

        if status not in {"resolved", "failed", "cancelled", "stale"}:
            raise ValueError(f"Unsupported owner intervention status: {status}")
        screenshot_path: Path | None = None
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT screenshot_name FROM owner_interventions WHERE challenge_id = ?",
                (challenge_id,),
            ).fetchone()
            if row is not None:
                screenshot_path = self._screenshot_path(str(row[0]))
            connection.execute(
                """
                UPDATE owner_interventions
                SET status = ?, answer = NULL, finished_at = ?
                WHERE challenge_id = ?
                """,
                (status, datetime.now(UTC).isoformat(), challenge_id),
            )
        if screenshot_path is not None and screenshot_path.is_relative_to(self.screenshot_dir):
            screenshot_path.unlink(missing_ok=True)

    def cancel_pending(self) -> None:
        """Close challenges abandoned by an earlier worker process."""

        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT challenge_id FROM owner_interventions
                WHERE status IN ('pending', 'answered', 'processing')
                """
            ).fetchall()
        for row in rows:
            self.finish(str(row[0]), "cancelled")

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS owner_interventions (
                    challenge_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    screenshot_name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    answer TEXT,
                    answered_at TEXT,
                    answer_consumed_at TEXT,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS owner_intervention_notifications (
                    challenge_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (challenge_id, user_id),
                    FOREIGN KEY (challenge_id) REFERENCES owner_interventions(challenge_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_owner_intervention_reply
                ON owner_intervention_notifications(user_id, message_id);
                """
            )
        self._restrict(self.database_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _screenshot_path(self, name: str) -> Path:
        path = (self.screenshot_dir / name).resolve()
        if not path.is_relative_to(self.screenshot_dir):
            raise ValueError("Owner intervention screenshot is outside the state directory")
        return path

    @staticmethod
    def _restrict(path: Path, mode: int) -> None:
        try:
            path.chmod(mode)
        except OSError:
            pass
