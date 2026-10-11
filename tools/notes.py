"""The user's notes on models and maps (local-data/notes.json), for agents to read later.

The file is a list of {page, subject, text, updated}: page is "models/<game>" for the model browser or a map
page's path ("/maps/t1/"), subject the model or map name. One note per page and subject; empty text deletes it.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

from tools.texture_workshop import write_json_atomic

_lock = threading.Lock()


def read_notes(path):
    path = Path(path)
    if not path.exists():
        return []
    notes = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(notes, list) or not all(isinstance(n, dict) and isinstance(n.get('page'), str) and isinstance(n.get('subject'), str)
                                              and isinstance(n.get('text'), str) for n in notes):
        raise ValueError('Invalid notes file; existing notes were preserved')
    return notes


def save_note(path, page, subject, text):
    if not all(isinstance(v, str) for v in (page, subject, text)) or not 0 < len(page) <= 200 or len(subject) > 300:
        raise ValueError('Expected a page, a subject and the note text')
    if len(text) > 100_000:
        raise ValueError('Notes are at most 100,000 characters')
    with _lock:
        notes = [n for n in read_notes(path) if (n['page'], n['subject']) != (page, subject)]
        note = None
        if text.strip():
            note = dict(page=page, subject=subject, text=text, updated=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            notes.append(note)
        write_json_atomic(path, sorted(notes, key=lambda n: (n['page'], n['subject'].casefold())))
    return note
