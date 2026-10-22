#!/usr/bin/env python3
"""Fetch word definitions from Wiktionary and build an Anki deck.

Usage:
    python make_deck.py hello world serendipity -o words.apkg
    python make_deck.py -f words.txt --lang fr --deck "French vocab"
    python make_deck.py -f words.txt --tsv words.tsv     # plain-text import file instead

Requires: pip install requests genanki   (genanki is only needed for .apkg output)
"""

import argparse
import hashlib
import html
import re
import sys
import time
from urllib.parse import quote

import requests

API = "https://{wiki}.wiktionary.org/api/rest_v1/page/definition/{word}"
# Wikimedia asks for a descriptive User-Agent; requests without one may be blocked.
HEADERS = {"User-Agent": "wikt2anki/1.0 (personal flashcard script)"}


def strip_html(text):
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def fetch_definitions(word, lang="en", wiki="en", max_defs=3, examples=True, session=None):
    """Return a list of (part_of_speech, definition, [examples]) for `word` in `lang`."""
    session = session or requests.Session()
    url = API.format(wiki=wiki, word=quote(word.replace(" ", "_"), safe=""))
    resp = session.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 429:
        # Rate-limited: wait as asked (Retry-After may be seconds or an HTTP date) and retry once.
        retry_after = resp.headers.get("Retry-After", "")
        wait = min(int(retry_after), 60) if retry_after.isdigit() else 5
        print(f"  rate-limited, retrying in {wait}s", file=sys.stderr)
        time.sleep(wait)
        resp = session.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()

    results = []
    for entry in data.get(lang, []):
        if entry.get("language") == "Translingual":
            continue  # ISO codes, chemical symbols, etc. listed alongside the real entries
        pos = entry.get("partOfSpeech", "")
        for d in entry.get("definitions", []):
            # Sub-senses are nested inside their parent and also listed separately; keep only the parent's own text.
            raw = re.sub(r"<(ol|ul)\b.*", "", d.get("definition", ""), flags=re.S)
            definition = strip_html(raw)
            if not definition:
                continue
            exs = [strip_html(e) for e in d.get("examples", [])] if examples else []
            results.append((pos, definition, [e for e in exs if e][:2]))
            if len(results) >= max_defs:
                return results
    return results


def format_back(defs):
    """Render definitions as HTML for the back of the card."""
    parts, last_pos = [], None
    for pos, definition, exs in defs:
        if pos != last_pos:
            if last_pos is not None:
                parts.append("</ol>")
            parts.append(f"<b><i>{html.escape(pos)}</i></b><ol>")
            last_pos = pos
        item = html.escape(definition)
        for e in exs:
            item += f"<br><span style='color:#888'>“{html.escape(e)}”</span>"
        parts.append(f"<li>{item}</li>")
    parts.append("</ol>")
    return "".join(parts)


def stable_id(name):
    # genanki needs fixed integer IDs so re-imports update the same deck instead of duplicating it.
    return int(hashlib.sha1(name.encode()).hexdigest()[:8], 16) | (1 << 30)


def write_apkg(cards, deck_name, out_path, reverse=False):
    import genanki

    templates = [{
        "name": "Word -> Definition",
        "qfmt": "<div class='word'>{{Word}}</div>",
        "afmt": "{{FrontSide}}<hr id='answer'>{{Definition}}",
    }]
    if reverse:
        templates.append({
            "name": "Definition -> Word",
            "qfmt": "{{Definition}}",
            "afmt": "{{FrontSide}}<hr id='answer'><div class='word'>{{Word}}</div>",
        })

    model = genanki.Model(
        stable_id("wikt2anki-model" + ("-rev" if reverse else "")),
        "Wiktionary Word" + (" (and reversed)" if reverse else ""),
        fields=[{"name": "Word"}, {"name": "Definition"}, {"name": "Source"}],
        templates=templates,
        css=(".card { font-family: sans-serif; font-size: 18px; text-align: left; }"
             ".word { font-size: 32px; text-align: center; font-weight: bold; }"),
    )
    deck = genanki.Deck(stable_id(deck_name), deck_name)
    for word, back, source in cards:
        deck.add_note(genanki.Note(
            model=model,
            fields=[html.escape(word), back, source],
            guid=genanki.guid_for(deck_name, word),
        ))
    genanki.Package(deck).write_to_file(out_path)


def write_tsv(cards, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("#separator:tab\n#html:true\n")
        for word, back, source in cards:
            f.write(f"{word}\t{back.replace(chr(9), ' ')}\t{source}\n")


def main():
    p = argparse.ArgumentParser(description="Make Anki flashcards from Wiktionary definitions.")
    p.add_argument("words", nargs="*", help="words to look up")
    p.add_argument("-f", "--file", help="text file with one word per line")
    p.add_argument("-l", "--lang", default="en",
                   help="language code of the words' entries, e.g. en, fr, de, es, ja (default: en)")
    p.add_argument("--wiki", default="en",
                   help="which Wiktionary to query, i.e. the language of the definitions (default: en)")
    p.add_argument("-d", "--deck", default="Wiktionary Vocabulary", help="Anki deck name")
    p.add_argument("-o", "--out", default="wiktionary.apkg", help="output .apkg path")
    p.add_argument("--tsv", help="write a tab-separated text file for Anki's File > Import instead")
    p.add_argument("-n", "--max-defs", type=int, default=3, help="max definitions per word")
    p.add_argument("--no-examples", action="store_true", help="omit usage examples")
    p.add_argument("--reverse", action="store_true", help="also make definition -> word cards")
    p.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    args = p.parse_args()

    words = list(args.words)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            words += [line.strip() for line in f if line.strip() and not line.startswith("#")]
    words = list(dict.fromkeys(words))  # dedupe, keep order
    if not words:
        p.error("give some words or --file")

    session = requests.Session()
    cards, missing = [], []
    for i, word in enumerate(words, 1):
        try:
            defs = fetch_definitions(word, args.lang, args.wiki, args.max_defs,
                                     not args.no_examples, session)
        except requests.RequestException as e:
            print(f"[{i}/{len(words)}] {word}: error ({e})", file=sys.stderr)
            missing.append(word)
            continue
        if defs:
            source = f"https://{args.wiki}.wiktionary.org/wiki/{quote(word.replace(' ', '_'))}"
            cards.append((word, format_back(defs), source))
            print(f"[{i}/{len(words)}] {word}: {len(defs)} definition(s)")
        else:
            print(f"[{i}/{len(words)}] {word}: no '{args.lang}' entry found", file=sys.stderr)
            missing.append(word)
        time.sleep(args.delay)

    if not cards:
        sys.exit("No cards made.")
    if args.tsv:
        write_tsv(cards, args.tsv)
        print(f"Wrote {len(cards)} cards to {args.tsv}")
    else:
        write_apkg(cards, args.deck, args.out, args.reverse)
        print(f"Wrote {len(cards)} cards to {args.out} (deck '{args.deck}')")
    if missing:
        print("Not found: " + ", ".join(missing), file=sys.stderr)


if __name__ == "__main__":
    main()
