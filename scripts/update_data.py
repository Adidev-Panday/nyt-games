#!/usr/bin/env python3
"""
Daily update script for NYT Games puzzle data.
Appends only new entries to data.json. Fully idempotent — safe to run multiple times.

Usage (from repo root):  python scripts/update_data.py
"""
import json
import re
import sys
import os
from collections import Counter
from datetime import date, timedelta

import requests

# data.json lives at the repo root (parent of scripts/)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JSON = os.path.join(REPO_ROOT, "data.json")

TODAY   = date.today()
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; nyt-puzzle-updater/1.0)"}
TIMEOUT = 15

COLOR_MAP = {0: "yellow", 1: "green", 2: "blue", 3: "purple"}

BLANK_ENTRY = {
    "wordle": None,
    "connections": None,
    "spelling_bee": None,
    "crossword": None,
    "strands": None,
}


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_data():
    with open(DATA_JSON, encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)

def ensure_entry(data, iso):
    if iso not in data:
        data[iso] = dict(BLANK_ENTRY)


# ── Wordle ────────────────────────────────────────────────────────────────────

def fetch_wordle_for_date(d):
    url = (
        f"https://raw.githubusercontent.com/TheDude53/wordle-answers/master/"
        f"{d.year}/{d.month:02d}/{d.day:02d}"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            word = r.text.strip().upper()
            if len(word) == 5 and word.isalpha():
                return word
    except Exception:
        pass
    return None

def update_wordle(data):
    added = []
    # Check the last 7 days so we catch any dates the repo added retroactively
    for i in range(7, -1, -1):
        d   = TODAY - timedelta(days=i)
        iso = d.isoformat()
        if data.get(iso, {}).get("wordle"):
            continue
        word = fetch_wordle_for_date(d)
        if word:
            ensure_entry(data, iso)
            data[iso]["wordle"] = word
            added.append(iso)
    return added


# ── Connections ───────────────────────────────────────────────────────────────

def parse_connections_entry(entry):
    raw_ans     = entry.get("answers", [])
    all_unknown = all(a.get("level", -1) == -1 for a in raw_ans)
    sorted_ans  = raw_ans if all_unknown else sorted(raw_ans, key=lambda x: x.get("level", 0))
    groups = []
    for i, ans in enumerate(sorted_ans):
        level = ans.get("level", -1)
        color = COLOR_MAP.get(level) if level != -1 else COLOR_MAP.get(i, "yellow")
        groups.append({
            "color":    color,
            "category": ans.get("group", ""),
            "words":    ans.get("members", []),
        })
    return groups or None

def update_connections(data):
    added = []
    url = "https://raw.githubusercontent.com/Eyefyre/NYT-Connections-Answers/main/connections.json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        entries = r.json()
    except Exception as e:
        print(f"  [warn] Connections fetch failed: {e}", file=sys.stderr)
        return added

    for entry in entries:
        iso = entry.get("date")
        if not iso:
            continue
        if data.get(iso, {}).get("connections"):
            continue  # Already present — skip
        groups = parse_connections_entry(entry)
        if groups:
            ensure_entry(data, iso)
            data[iso]["connections"] = groups
            added.append(iso)
    return added


# ── Spelling Bee ──────────────────────────────────────────────────────────────

def parse_bee_html(html):
    """Parse nytbee.com HTML, handling both the old and new page layouts."""
    sec_m = re.search(
        r'id="main-answer-list"(.*?)(?:id="other-words"|id="this-puzzle-plots"|$)',
        html, re.DOTALL,
    )
    if not sec_m:
        return None
    sec = sec_m.group(1)

    # New layout: flex-list-item divs (strip inner tags to get the word)
    flex_divs = re.findall(r'<div class="flex-list-item">(.*?)</div>', sec, re.DOTALL)
    explicit_pangrams = []

    if flex_divs:
        raw = []
        for div in flex_divs:
            text = re.sub(r"<[^>]+>", "", div).strip()
            word = text.split()[0] if text.split() else ""
            if word.isalpha():
                raw.append(word)
    else:
        # Old layout: <ul class="column-list"> with <li> items
        # Pangrams are wrapped in <mark><strong>word</strong></mark>
        explicit_pangrams = [
            p.upper()
            for p in re.findall(
                r"<mark>\s*<strong>\s*([A-Za-z]+)\s*</strong>\s*</mark>", sec
            )
        ]
        raw = re.findall(
            r"<li[^>]*>\s*(?:<[^>]+>)*\s*([A-Za-z]+)\s*(?:<[^>]+>)*\s*</li>", sec
        )

    answers = sorted(set(w.upper() for w in raw if len(w) >= 4))
    if not answers:
        return None

    letter_sets  = [set(w) for w in answers]
    all_letters  = set.union(*letter_sets)
    center_cands = set.intersection(*letter_sets)

    if center_cands:
        center = max(center_cands, key=lambda c: sum(1 for w in answers if c in w))
    else:
        freq   = Counter(ch for w in answers for ch in w)
        center = freq.most_common(1)[0][0]

    outer    = sorted(all_letters - {center})
    pangrams = explicit_pangrams or [w for w in answers if all_letters <= set(w)]

    return {"center": center, "letters": outer, "pangrams": pangrams, "answers": answers}

def fetch_bee_for_date(d):
    url = f"https://nytbee.com/Bee_{d.strftime('%Y%m%d')}.html"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            return parse_bee_html(r.text)
    except Exception:
        pass
    return None

def update_spelling_bee(data):
    added = []
    # Try today then yesterday — nytbee sometimes lags by a day
    for i in range(2):
        d   = TODAY - timedelta(days=i)
        iso = d.isoformat()
        if data.get(iso, {}).get("spelling_bee"):
            continue
        result = fetch_bee_for_date(d)
        if result:
            ensure_entry(data, iso)
            data[iso]["spelling_bee"] = result
            added.append(iso)
    return added


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Loading data.json from {DATA_JSON}")
    try:
        data = load_data()
    except Exception as e:
        print(f"ERROR: could not load data.json: {e}", file=sys.stderr)
        return 1
    print(f"  {len(data)} existing entries")

    changed = False

    # Wordle
    print("Checking Wordle...")
    wordle_added = update_wordle(data)
    if wordle_added:
        for iso in wordle_added:
            print(f"  Added Wordle for {iso}: {data[iso]['wordle']}")
        changed = True
    else:
        print("  Nothing new for Wordle")

    # Connections
    print("Checking Connections...")
    conn_added = update_connections(data)
    if conn_added:
        for iso in sorted(conn_added):
            print(f"  Added Connections for {iso}")
        changed = True
    else:
        print("  Nothing new for Connections")

    # Spelling Bee
    print("Checking Spelling Bee...")
    bee_added = update_spelling_bee(data)
    if bee_added:
        for iso in bee_added:
            bee = data[iso]["spelling_bee"]
            print(f"  Added Spelling Bee for {iso}: center={bee['center']}, {len(bee['answers'])} answers")
        changed = True
    else:
        print("  Nothing new for Spelling Bee")

    # Save
    if changed:
        try:
            save_data(data)
            print(f"Saved data.json ({len(data)} total entries)")
        except Exception as e:
            print(f"ERROR: could not save data.json: {e}", file=sys.stderr)
            return 1
    else:
        print("No changes — data.json not modified")

    return 0


if __name__ == "__main__":
    sys.exit(main())
