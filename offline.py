#!/usr/bin/env python3
"""Look up Wiktionary definitions offline, from a kaikki.org (wiktextract) download.

Build a small lookup database once, then pass --offline to make_deck.py or chat.py:

    python offline.py build raw-wiktextract-data.jsonl.gz --lang fr --lang en
    python make_deck.py chat pomme --lang fr --offline
    python offline.py lookup pomme --lang fr      # quick check

The download comes from https://kaikki.org/dictionary/rawdata.html (all languages) or a
single-language page such as https://kaikki.org/dictionary/French/ (smaller, faster to build).
"""

import argparse
import gzip
import io
import json
import os
import sqlite3
import sys
import time

DEFAULT_DB = "wiktionary.db"
MAX_EXAMPLES = 2

# wiktextract's part-of-speech codes, spelled the way Wiktionary's own headings are.
POS_NAMES = {
    "adj": "Adjective", "adv": "Adverb", "prep": "Preposition", "postp": "Postposition",
    "pron": "Pronoun", "conj": "Conjunction", "intj": "Interjection", "num": "Numeral",
    "det": "Determiner", "name": "Proper noun", "abbrev": "Abbreviation",
    "prep_phrase": "Prepositional phrase", "adj_noun": "Adjective",
}


def pos_name(pos):
    return POS_NAMES.get(pos, pos.replace("_", " ").capitalize())


def compact_senses(senses):
    """Reduce wiktextract senses to [[gloss, [examples]], ...]: all the cards need."""
    out = []
    for s in senses:
        glosses = s.get("glosses")
        if not glosses or "no-gloss" in s.get("tags", []):
            continue
        # Sub-senses repeat their parent's gloss first; the last one is this sense's own text.
        gloss = glosses[-1]
        # Prefer short made-up examples over long literary quotations.
        exs = sorted(s.get("examples", []), key=lambda e: e.get("type") == "quotation")
        texts = []
        for e in exs:
            text = (e.get("text") or "").strip()
            if not text or len(text) > 300:
                continue
            translation = (e.get("translation") or e.get("english") or "").strip()
            texts.append(f"{text} — {translation}" if translation else text)
            if len(texts) == MAX_EXAMPLES:
                break
        out.append([gloss, texts])
    return out


def build(dump_path, db_path=DEFAULT_DB, langs=None):
    """Read a wiktextract .jsonl(.gz) file and store the entries for `langs` (all if None)."""
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE IF NOT EXISTS entries "
               "(word TEXT, word_lower TEXT, lang_code TEXT, pos TEXT, senses TEXT)")
    if langs:
        db.executemany("DELETE FROM entries WHERE lang_code = ?", [(l,) for l in langs])
    else:
        db.execute("DELETE FROM entries")
    # Indexes are much faster to create once at the end than to update on every insert.
    db.execute("DROP INDEX IF EXISTS by_word")
    db.execute("DROP INDEX IF EXISTS by_word_lower")
    # Cheap text check before parsing; lang_code also appears inside translations, so recheck after.
    needles = [f'"lang_code": "{l}"' for l in langs] if langs else None

    raw = open(dump_path, "rb")
    f = gzip.open(raw, "rt", encoding="utf-8") if dump_path.endswith(".gz") else \
        io.TextIOWrapper(raw, encoding="utf-8")
    total = os.path.getsize(dump_path)
    start = last = time.time()
    lines = kept = 0
    rows = []
    with f:
        for line in f:
            lines += 1
            if time.time() - last > 5:
                last = time.time()
                print(f"\r  {raw.tell() / total:6.1%} read, {kept:,} entries kept, "
                      f"{last - start:.0f}s", end="", flush=True)
            if needles and not any(n in line for n in needles):
                continue
            d = json.loads(line)
            if "word" not in d or "senses" not in d:
                continue  # redirects and other non-entry pages
            if langs and d.get("lang_code") not in langs:
                continue
            senses = compact_senses(d["senses"])
            if not senses:
                continue
            word = d["word"]
            rows.append((word, word.lower(), d.get("lang_code"), pos_name(d.get("pos", "")),
                         json.dumps(senses, ensure_ascii=False)))
            kept += 1
            if len(rows) >= 10000:
                db.executemany("INSERT INTO entries VALUES (?, ?, ?, ?, ?)", rows)
                rows.clear()
    db.executemany("INSERT INTO entries VALUES (?, ?, ?, ?, ?)", rows)
    print(f"\r  100.0% read, {kept:,} entries kept, {time.time() - start:.0f}s. Indexing...",
          flush=True)
    db.execute("CREATE INDEX by_word ON entries (lang_code, word)")
    db.execute("CREATE INDEX by_word_lower ON entries (lang_code, word_lower)")
    db.commit()
    counts = db.execute("SELECT lang_code, COUNT(*) FROM entries GROUP BY lang_code "
                        "ORDER BY COUNT(*) DESC LIMIT 10").fetchall()
    db.close()
    return kept, counts


_connections = {}


def connect(db_path=DEFAULT_DB):
    if db_path not in _connections:
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"{db_path} not found. Build it first: python offline.py build <kaikki file> --lang ...")
        # check_same_thread=False: the chat's tool runner may call lookups from another thread.
        _connections[db_path] = sqlite3.connect(db_path, check_same_thread=False)
    return _connections[db_path]


def fetch_definitions(word, lang="en", max_defs=3, examples=True, db_path=DEFAULT_DB):
    """Offline twin of make_deck.fetch_definitions: [(part_of_speech, definition, [examples])]."""
    db = connect(db_path)
    rows = db.execute("SELECT pos, senses FROM entries WHERE lang_code = ? AND word = ? "
                      "ORDER BY rowid", (lang, word)).fetchall()
    if not rows:  # fall back to a different capitalisation, e.g. "haus" -> "Haus"
        rows = db.execute("SELECT pos, senses FROM entries WHERE lang_code = ? AND word_lower = ? "
                          "ORDER BY rowid", (lang, word.lower())).fetchall()
    results, seen = [], set()
    for pos, senses in rows:
        for gloss, exs in json.loads(senses):
            if (pos, gloss) in seen:
                continue  # e.g. transitive and intransitive senses that share one gloss
            seen.add((pos, gloss))
            results.append((pos, gloss, exs if examples else []))
            if len(results) >= max_defs:
                return results
    return results


def main():
    p = argparse.ArgumentParser(description="Offline Wiktionary lookups from a kaikki.org download.")
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build the lookup database from a kaikki.org .jsonl or .jsonl.gz file")
    b.add_argument("dump", help="path to the kaikki.org download")
    b.add_argument("-l", "--lang", action="append",
                   help="language code to include (repeatable, e.g. --lang fr --lang en); default: all")
    b.add_argument("--db", default=DEFAULT_DB, help=f"database to write (default: {DEFAULT_DB})")
    q = sub.add_parser("lookup", help="print a word's definitions from the database")
    q.add_argument("word")
    q.add_argument("-l", "--lang", default="en")
    q.add_argument("-n", "--max-defs", type=int, default=5)
    q.add_argument("--db", default=DEFAULT_DB)
    args = p.parse_args()

    if args.command == "build":
        if not args.lang:
            print("No --lang given: including every language. This takes much longer and makes "
                  "a much bigger database.", file=sys.stderr)
        kept, counts = build(args.dump, args.db, args.lang)
        print(f"Saved {kept:,} entries to {args.db}: "
              + ", ".join(f"{code} {n:,}" for code, n in counts))
    else:
        try:
            defs = fetch_definitions(args.word, args.lang, args.max_defs, db_path=args.db)
        except FileNotFoundError as e:
            sys.exit(str(e))
        if not defs:
            sys.exit(f"No '{args.lang}' entry for {args.word!r} in {args.db}.")
        for pos, gloss, exs in defs:
            print(f"{pos}: {gloss}")
            for e in exs:
                print(f"    “{e}”")


if __name__ == "__main__":
    main()
