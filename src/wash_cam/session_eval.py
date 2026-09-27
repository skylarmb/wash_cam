"""Score predicted bay sessions against sessions derived from human labels."""

import json
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from wash_cam.config import BAYS, CAPTURE_INTERVAL, DATABASE_PATH
from wash_cam.sessions import (
    CLOSE_FRAMES,
    OPEN_FRAMES,
    Frame,
    Session,
    build_sessions,
)

__all__ = ["SessionScore", "evaluate_sessions", "run", "score_sessions"]


@dataclass(frozen=True)
class SessionScore:
    """How well predicted sessions reproduce the labeled ones."""

    true_sessions: int
    predicted_sessions: int
    # True sessions paired with an overlapping prediction.
    matched: int
    # True sessions with no overlapping prediction.
    missed: int
    # Predictions with no overlapping true session.
    phantom: int
    # Mean absolute timing errors over matched pairs, in seconds.
    mean_start_error_s: float
    mean_end_error_s: float
    mean_duration_error_s: float
    # Total occupied time: the quantity revenue is estimated from.
    true_minutes: float
    predicted_minutes: float


def overlap_s(first: Session, second: Session) -> float:
    """Return how many seconds two sessions overlap, or 0 if they do not."""
    start = max(first.started_at, second.started_at)
    end = min(first.ended_at, second.ended_at)
    return max((end - start).total_seconds(), 0.0)


def score_sessions(
    truth: Sequence[Session], predicted: Sequence[Session]
) -> SessionScore:
    """Pair each true session with its most-overlapping unused prediction, then score.

    Each prediction pairs with at most one true session, so a prediction that
    merges two real visits counts one match and one miss.
    """
    unused = list(predicted)
    pairs: list[tuple[Session, Session]] = []
    for real in truth:
        best = max(unused, key=lambda guess: overlap_s(real, guess), default=None)
        if best is not None and overlap_s(real, best) > 0:
            pairs.append((real, best))
            unused.remove(best)

    def mean_error(error: Callable[[Session, Session], float]) -> float:
        """Average an absolute error over matched pairs, or 0 with none."""
        if not pairs:
            return 0.0
        return sum(error(real, guess) for real, guess in pairs) / len(pairs)

    return SessionScore(
        true_sessions=len(truth),
        predicted_sessions=len(predicted),
        matched=len(pairs),
        missed=len(truth) - len(pairs),
        phantom=len(unused),
        mean_start_error_s=mean_error(
            lambda real, guess: abs(
                (guess.started_at - real.started_at).total_seconds()
            )
        ),
        mean_end_error_s=mean_error(
            lambda real, guess: abs((guess.ended_at - real.ended_at).total_seconds())
        ),
        mean_duration_error_s=mean_error(
            lambda real, guess: abs(guess.duration_s - real.duration_s)
        ),
        true_minutes=sum(session.duration_s for session in truth) / 60,
        predicted_minutes=sum(session.duration_s for session in predicted) / 60,
    )


def combine(scores: Sequence[SessionScore]) -> SessionScore:
    """Sum per-bay scores, weighting timing errors by each bay's matches.

    Sessions are matched within a bay, never across bays, so the overall score
    is built from per-bay results rather than by pooling every session.
    """
    matched = sum(score.matched for score in scores)

    def weighted(error: Callable[[SessionScore], float]) -> float:
        """Average a per-bay mean error, weighted by matched sessions."""
        if not matched:
            return 0.0
        return sum(error(score) * score.matched for score in scores) / matched

    return SessionScore(
        true_sessions=sum(score.true_sessions for score in scores),
        predicted_sessions=sum(score.predicted_sessions for score in scores),
        matched=matched,
        missed=sum(score.missed for score in scores),
        phantom=sum(score.phantom for score in scores),
        mean_start_error_s=weighted(lambda score: score.mean_start_error_s),
        mean_end_error_s=weighted(lambda score: score.mean_end_error_s),
        mean_duration_error_s=weighted(lambda score: score.mean_duration_error_s),
        true_minutes=sum(score.true_minutes for score in scores),
        predicted_minutes=sum(score.predicted_minutes for score in scores),
    )


def bay_counts(metadata: str) -> dict[str, int]:
    """Return the known bays' vehicle counts from label or detection metadata."""
    entries = json.loads(metadata).get("vehicles", {}).get("bays", [])
    return {
        entry["bay_id"]: entry["vehicle_count"]
        for entry in entries
        if entry.get("bay_id") in BAYS and isinstance(entry.get("vehicle_count"), int)
    }


def paired_frames(database: Path) -> dict[str, tuple[list[Frame], list[Frame]]]:
    """Return each bay's (labeled, detected) frames over the same labeled captures.

    Only captures with both a human label and a timestamped detection are used,
    and a bay is included in a capture only when the label covers it.
    """
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT d.captured_at, l.metadata, d.metadata "
            "FROM image_labels AS l JOIN image_detections AS d USING (image_path) "
            "WHERE d.captured_at IS NOT NULL ORDER BY d.captured_at"
        ).fetchall()

    frames: dict[str, tuple[list[Frame], list[Frame]]] = {
        bay_id: ([], []) for bay_id in BAYS
    }
    for captured_at, label_metadata, detection_metadata in rows:
        # Stored times are UTC without an offset.
        moment = datetime.fromisoformat(captured_at).replace(tzinfo=timezone.utc)
        labeled, detected = bay_counts(label_metadata), bay_counts(detection_metadata)
        for bay_id in labeled.keys() & detected.keys():
            frames[bay_id][0].append((moment, labeled[bay_id]))
            frames[bay_id][1].append((moment, detected[bay_id]))
    return frames


def evaluate_sessions(
    database: Path,
    interval: float = CAPTURE_INTERVAL,
    open_frames: int = OPEN_FRAMES,
    close_frames: int = CLOSE_FRAMES,
) -> dict[str, SessionScore]:
    """Score predicted sessions per bay and overall ("all") on labeled captures.

    True sessions come from the labels with no debouncing, since human labels
    have no one-frame errors. Predicted sessions come from the detections on
    exactly the same frames, debounced with the given thresholds. Runs of
    labeled frames separated by a capture gap are scored as separate windows,
    because build_sessions ends sessions at gaps on both sides.
    """
    scores = {}
    for bay_id, (labeled, detected) in paired_frames(database).items():
        truth = build_sessions(bay_id, labeled, interval, 1, 1)
        predicted = build_sessions(
            bay_id, detected, interval, open_frames, close_frames
        )
        scores[bay_id] = score_sessions(truth, predicted)
    scores["all"] = combine(list(scores.values()))
    return scores


def run(open_frames: int = OPEN_FRAMES, close_frames: int = CLOSE_FRAMES) -> None:
    """Print session scores for the server database with the given thresholds."""
    scores = evaluate_sessions(
        DATABASE_PATH, open_frames=open_frames, close_frames=close_frames
    )
    print(f"Thresholds: open after {open_frames}, close after {close_frames} frames")
    print(
        f"{'bay':<6} {'true':>5} {'pred':>5} {'match':>6} {'miss':>5} {'phantom':>8} "
        f"{'start s':>8} {'end s':>6} {'dur s':>6} {'true min':>9} {'pred min':>9}"
    )
    for bay_id, score in scores.items():
        print(
            f"{bay_id:<6} {score.true_sessions:>5} {score.predicted_sessions:>5} "
            f"{score.matched:>6} {score.missed:>5} {score.phantom:>8} "
            f"{score.mean_start_error_s:>8.1f} {score.mean_end_error_s:>6.1f} "
            f"{score.mean_duration_error_s:>6.1f} {score.true_minutes:>9.1f} "
            f"{score.predicted_minutes:>9.1f}"
        )
    # Carried-forward counts saved without review look like ground truth here.
    print(
        "Labels are only as reliable as the labeling session: counts carried "
        "forward in the labeling tab and saved unchecked are scored as truth."
    )
