#!/usr/bin/env python3
"""Chat with an AI to build an Anki deck from Wiktionary definitions.

Ask for words in plain language ("20 French kitchen words", "the hard words in this
paragraph: ...") and the AI picks them, looks them up with make_deck.py, and saves the deck.
Works with Claude, OpenAI or Gemini: paste your API key at startup and the script works
out which provider it belongs to.

Usage:
    python chat.py                       # asks for a key, unless one is set in the environment
    python chat.py --provider gemini
    python chat.py --provider openai --model gpt-5

Requires: pip install anthropic openai requests genanki, and an API key. Setting
ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY skips the prompt.
"""

import argparse
import getpass
import json
import os
import sys
import time
from urllib.parse import quote

import anthropic
import requests
from anthropic import beta_tool

from make_deck import fetch_definitions, format_back, write_apkg, write_tsv

# provider: (API key variable, default model)
PROVIDERS = {
    "claude": ("ANTHROPIC_API_KEY", "claude-opus-5-5"),
    "openai": ("OPENAI_API_KEY", "gpt-5"),
    "gemini": ("GEMINI_API_KEY", "gemini-3.5-flash"),
}
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
SYSTEM = """You help the user build Anki flashcard decks of vocabulary.

You can look up words on English Wiktionary, keep a list of the words that were found, \
and save that list as an Anki deck. Definitions are always in English. The `lang` \
argument picks which language's entry is used (en, fr, de, es, ja, ...), so a French \
learner's words need lang="fr".

When the user describes what they want (a theme, a level, a text to mine), choose \
suitable words yourself and look them up. Use dictionary forms (infinitives, singular \
nouns), since Wiktionary entries for inflected forms only say "plural of ...". If a word \
is not found or its definitions don't match what the user meant, try a better word or \
say so. Keep replies short: this is a terminal chat. Save the deck only when the user \
asks you to."""

# Cards collected so far, keyed by word: (back_html, source_url, lang).
cards = {}
unsaved = False  # cards changed since the last save
session = requests.Session()


@beta_tool
def lookup_words(words: list[str], lang: str = "en", max_defs: int = 3) -> str:
    """Look up words on English Wiktionary and add every word that is found to the deck.

    Returns each word's definitions as plain text, or why it was not added.

    Args:
        words: Words or phrases to look up, in their dictionary form.
        lang: Language code of the entries to use, e.g. en, fr, de, es, ja.
        max_defs: Maximum number of definitions to keep per word (1-10).
    """
    global unsaved
    max_defs = max(1, min(max_defs, 10))
    lines = []
    for word in words:
        word = word.strip()
        if not word:
            continue
        try:
            defs = fetch_definitions(word, lang, max_defs=max_defs, session=session)
        except requests.RequestException as e:
            lines.append(f"{word}: error ({e})")
            print(f"  {word}: error", file=sys.stderr)
            continue
        if defs:
            source = f"https://en.wiktionary.org/wiki/{quote(word.replace(' ', '_'))}"
            cards[word] = (format_back(defs), source, lang)
            unsaved = True
            summary = "; ".join(f"({pos}) {d}" for pos, d, _ in defs)
            lines.append(f"{word}: added. {summary}")
            print(f"  {word}: {len(defs)} definition(s)")
        else:
            lines.append(f"{word}: no '{lang}' entry found, not added")
            print(f"  {word}: not found", file=sys.stderr)
        time.sleep(0.5)  # be polite to Wiktionary
    lines.append(f"Deck now has {len(cards)} word(s).")
    return "\n".join(lines)


@beta_tool
def remove_words(words: list[str]) -> str:
    """Remove words from the deck.

    Args:
        words: The words to remove, spelled as they were added.
    """
    global unsaved
    removed = [w for w in words if cards.pop(w, None)]
    unsaved = unsaved or bool(removed)
    missing = [w for w in words if w not in removed]
    result = f"Removed: {', '.join(removed) or 'nothing'}. Deck now has {len(cards)} word(s)."
    if missing:
        result += f" Not in the deck: {', '.join(missing)}."
    return result


@beta_tool
def list_words() -> str:
    """List the words currently in the deck, with their language codes."""
    if not cards:
        return "The deck is empty."
    return f"{len(cards)} word(s): " + ", ".join(f"{w} ({c[2]})" for w, c in cards.items())


@beta_tool
def save_deck(deck_name: str = "Wiktionary Vocabulary", filename: str = "wiktionary.apkg",
              reverse: bool = False) -> str:
    """Save the deck to a file in the current folder.

    Args:
        deck_name: Name of the deck inside Anki.
        filename: File name ending in .apkg (Anki package) or .tsv (plain-text import file).
        reverse: Also make definition -> word cards. Only applies to .apkg files.
    """
    global unsaved
    if not cards:
        return "Not saved: the deck is empty."
    filename = os.path.basename(filename)  # never write outside the current folder
    rows = [(word, back, source) for word, (back, source, _) in cards.items()]
    if filename.endswith(".tsv"):
        write_tsv(rows, filename)
    elif filename.endswith(".apkg"):
        write_apkg(rows, deck_name, filename, reverse)
    else:
        return "Not saved: the file name must end in .apkg or .tsv."
    unsaved = False
    path = os.path.abspath(filename)
    print(f"  saved {len(rows)} card(s) to {path}")
    if filename.endswith(".tsv"):
        return f"Saved {len(rows)} card(s) to {path}."
    return f"Saved {len(rows)} card(s) to {path} (deck '{deck_name}')."


TOOLS = [lookup_words, remove_words, list_words, save_deck]


def run_turn_claude(client, model, messages):
    """Send the conversation to Claude and run tools until it replies. Appends to `messages`."""
    runner = client.beta.messages.tool_runner(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        tools=TOOLS,
        messages=messages,
        output_config={"effort": "medium"},
        # If a request is declined by a safety classifier, retry it on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    for message in runner:
        # The runner keeps its own copy of the history, so mirror it for the next turn.
        messages.append({"role": "assistant", "content": message.content})
        for block in message.content:
            if block.type == "text" and block.text.strip():
                print(f"\nai> {block.text.strip()}")
        if message.stop_reason == "refusal":
            print("\nai> (declined to answer this request)")
        tool_response = runner.generate_tool_call_response()  # cached, so tools run once
        if tool_response is not None:
            messages.append(tool_response)


def run_turn_openai(client, model, messages):
    """Same as run_turn_claude, for OpenAI's Chat Completions API (also used for Gemini)."""
    tools = [{"type": "function",
              "function": {"name": t.name, "description": t.description,
                           "parameters": t.input_schema}}
             for t in TOOLS]
    by_name = {t.name: t for t in TOOLS}
    while True:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM}, *messages],
            tools=tools,
        )
        msg = resp.choices[0].message
        # Keep the whole message: Gemini attaches thought signatures it needs back next time.
        messages.append(msg.model_dump(exclude_none=True))
        if msg.content and msg.content.strip():
            print(f"\nai> {msg.content.strip()}")
        if not msg.tool_calls:
            return
        for call in msg.tool_calls:
            try:
                result = by_name[call.function.name].call(json.loads(call.function.arguments or "{}"))
            except Exception as e:  # report bad tool calls back to the model instead of crashing
                result = f"Error: {e}"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})


def make_client(provider, key):
    if provider == "claude":
        return anthropic, anthropic.Anthropic(api_key=key), run_turn_claude
    import openai
    base_url = GEMINI_BASE_URL if provider == "gemini" else None
    return openai, openai.OpenAI(api_key=key, base_url=base_url, timeout=120), run_turn_openai


def guess_provider(key):
    """Work out which provider an API key belongs to from its prefix, or None."""
    if key.startswith("sk-ant-"):
        return "claude"
    if key.startswith("sk-"):
        return "openai"
    if key.startswith(("AIza", "AQ.")):
        return "gemini"
    return None


def ask_for_key():
    prompt = "Paste your API key (Claude, OpenAI or Gemini): "
    try:
        # getpass hides the key as you paste it, but needs a real terminal.
        key = getpass.getpass(prompt) if sys.stdin.isatty() else input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return key.strip()


def ask_for_provider():
    while True:
        try:
            answer = input("Which provider is this key for? (claude/openai/gemini): ")
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if answer.strip().lower() in PROVIDERS:
            return answer.strip().lower()


def check_key(api, client):
    """Return why the key doesn't work, or None if it does. Listing models is free."""
    try:
        client.models.list()
    except api.APIStatusError as e:
        return f"That key was rejected (HTTP {e.status_code})"
    except api.APIConnectionError:
        sys.exit("Couldn't reach the API. Check your internet connection.")
    return None


def main():
    p = argparse.ArgumentParser(description="Chat with an AI to build an Anki deck.")
    p.add_argument("--provider", choices=PROVIDERS,
                   help="which AI to use (default: worked out from the API key)")
    p.add_argument("--model", help="model name (default depends on the provider)")
    args = p.parse_args()

    # A key already set in the environment skips the prompt.
    provider, key = args.provider, None
    for name, (env, _) in PROVIDERS.items():
        if provider in (None, name) and os.environ.get(env):
            provider, key = name, os.environ[env]
            print(f"Using the API key in {env}.")
            break
    from_env = key is not None

    while True:
        if key is None:
            key = ask_for_key()
            if not key:
                continue
            if args.provider is None:
                provider = guess_provider(key) or ask_for_provider()
        api, client, run_turn = make_client(provider, key)
        problem = check_key(api, client)
        if problem is None:
            break
        if from_env:
            sys.exit(f"{problem}. Check {PROVIDERS[provider][0]}.")
        print(f"{problem}. Try again.")
        key, provider = None, args.provider

    model = args.model or PROVIDERS[provider][1]
    messages = []
    print(f"Using {provider} ({model}). Tell me what words you want flashcards for. "
          "Type 'quit' to exit.")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit"):
            break

        turn_start = len(messages)
        messages.append({"role": "user", "content": text})
        try:
            run_turn(client, model, messages)
        except KeyboardInterrupt:
            print("\n(interrupted)")
        # anthropic and openai name their exceptions the same way.
        except api.AuthenticationError:
            sys.exit("Your API key was rejected.")
        except api.RateLimitError:
            print("\nRate-limited by the API. Wait a moment and try again.")
        except api.NotFoundError as e:
            print(f"\nModel or endpoint not found ({e.message}). Try a different --model.")
        except api.APIStatusError as e:
            print(f"\nAPI error ({e.status_code}): {e.message}")
        except api.APIConnectionError:
            print("\nCouldn't reach the API. Check your internet connection.")
        else:
            continue
        # The turn failed part-way: drop it so the history stays valid for the next one.
        del messages[turn_start:]

    if unsaved:
        print(f"Note: the deck's last changes weren't saved ({len(cards)} word(s) collected).")


if __name__ == "__main__":
    main()
