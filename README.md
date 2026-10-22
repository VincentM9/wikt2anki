# wikt2anki

Turn a list of words into Anki flashcards with definitions pulled from [Wiktionary](https://en.wiktionary.org).

Each card has the word on the front. On the back are its definitions, grouped by part of speech, with up to two usage examples for each definition. Each card also stores a link to the Wiktionary page it came from.

## Setup

You need Python 3.7 or newer.

```sh
pip install -r requirements.txt
```

`genanki` is only needed to write `.apkg` files, and `anthropic` and `openai` are only needed for [chat mode](#chat-mode-let-an-ai-pick-the-words). If you only use `--tsv`, `requests` is enough.

## Quick start

```sh
python make_deck.py hello world serendipity
```

This writes `wiktionary.apkg`. To use it, double-click the file or use **File > Import** in Anki. The cards go into a deck called "Wiktionary Vocabulary".

## Usage

```sh
# Words on the command line, custom output file
python make_deck.py hello world serendipity -o words.apkg

# Words from a file, French entries, custom deck name
python make_deck.py -f words.txt --lang fr --deck "French vocab"

# Plain-text import file instead of a .apkg
python make_deck.py -f words.txt --tsv words.tsv
```

### Word lists

Put one word or phrase per line. Blank lines and lines that start with `#` are skipped, and duplicates are removed, keeping the first occurrence.

```text
# chapter 3
serendipity
ephemeral
red herring
```

You can give words on the command line and `--file` together.

### Options

| Option | Default | Description |
|---|---|---|
| `-f`, `--file` | | Text file with one word per line |
| `-l`, `--lang` | `en` | Language of the words, as a code such as `en`, `fr`, `de`, `es` or `ja` (see below) |
| `-d`, `--deck` | `Wiktionary Vocabulary` | Name of the Anki deck |
| `-o`, `--out` | `wiktionary.apkg` | Path of the `.apkg` file to write |
| `--tsv` | | Write a tab-separated file for Anki's **File > Import** instead of a `.apkg` |
| `-n`, `--max-defs` | `3` | Maximum number of definitions per word |
| `--no-examples` | off | Leave out usage examples |
| `--reverse` | off | Also make definition → word cards (`.apkg` only) |
| `--delay` | `0.5` | Seconds to wait between requests |
| `--wiki` | `en` | Which Wiktionary to query. Only `en` works at present (see [Limitations](#limitations)) |

### Learning other languages

`--lang` chooses which language's entry to read from the Wiktionary page. The definitions are always in English. For example, `--lang fr` looks up French words and gives English definitions:

```sh
python make_deck.py chat chien maison --lang fr --deck "French vocab"
```

This matters for words that exist in more than one language. Without `--lang fr`, `chat` would get the English entry ("to be engaged in informal conversation") instead of "cat".

## Chat mode: let an AI pick the words

`chat.py` lets you describe what you want in plain language. The AI chooses suitable words, looks them up with the same Wiktionary code, and saves the deck when you ask it to. It works with Claude, OpenAI or Gemini. You need an API key for one of them:

| Provider | Environment variable | Get a key | Default model |
|---|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) | `claude-opus-5-5` |
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) | `gpt-5` |
| Gemini | `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/apikey) | `gemini-3.5-flash` |

Start the chat and paste your key when asked:

```
python chat.py
Paste your API key (Claude, OpenAI or Gemini):
```

The key stays hidden while you paste it. The script works out the provider from how the key starts:

- `sk-ant-` is Claude.
- Any other `sk-` is OpenAI.
- `AIza` or `AQ.` is Gemini.

If it can't tell, it asks you. Before the chat starts, it checks that the key works, and asks again if it doesn't.

To skip the prompt, set the key as an environment variable from the table above:

```powershell
$env:GEMINI_API_KEY = "your-key"     # macOS/Linux: export GEMINI_API_KEY=your-key
```

If more than one key is set, the script uses Claude, then OpenAI, then Gemini. You can also choose the provider and model yourself:

```powershell
python chat.py --provider gemini
python chat.py --provider openai --model gpt-5-mini
```

Here is a real session with Gemini:

```
you> 4 French words for fruit, beginner level
  pomme: 3 definition(s)
  banane: 3 definition(s)
  orange: 3 definition(s)
  fraise: 3 definition(s)

ai> I've added 4 beginner-level French words for fruit to your deck: ...

you> remove one of them, then save as fruit.apkg with deck name "Fruit test"
  saved 3 card(s) to C:\Users\you\fruit.apkg

ai> I've removed fraise and saved the remaining 3 words (pomme, banane, orange) ...
```

Other things you can ask for:

- **Words from a text:** paste a paragraph and ask for cards for the hard words in it.
- **A topic or level:** for example, "30 GRE words about arguing".
- **Changes to the list:** ask the AI to list what's in the deck so far, or to remove words.

The AI can only save `.apkg` or `.tsv` files, and only in the folder you ran the script from. Each message you send costs a small amount of API usage. To leave, type `quit`. If you quit with unsaved changes, the script tells you.

## Output

The script prints a progress line for each word:

```
[1/3] hello: 3 definition(s)
[2/3] asdfgh: no 'en' entry found
[3/3] world: 3 definition(s)
Wrote 2 cards to wiktionary.apkg (deck 'Wiktionary Vocabulary')
Not found: asdfgh
```

The "Not found" list at the end includes words that have no entry in the chosen language and words whose request failed. You can fix the spelling of those words and run them again.

### Re-importing

Note IDs come from the deck name and the word. If you run the script again with the same `--deck` and import the result, Anki updates the existing cards and doesn't create duplicates. This means you can keep adding to a word list and regenerate the whole deck.

Running with and without `--reverse` uses two different note types. Pick one setting for each deck and keep using it.

### `.apkg` or `--tsv`

- **`.apkg`** (the default) includes the note type, its styling and the deck. Importing it is a single step.
- **`--tsv`** writes plain text with three columns: Word, Definition (as HTML) and Source. Use it if you want to map the columns onto your own note type when you import. Anki 2.1.54 and later read the header lines in the file and set the separator and HTML options for you.

## Limitations

- **Definitions come only from English Wiktionary.** The script uses Wiktionary's REST definition API, which exists only on en.wiktionary.org. Other wikis respond with HTTP 501, so `--wiki fr` and similar values make every word fail.
- **Reverse cards can give away the answer.** Usage examples usually contain the word itself. If you use `--reverse`, consider adding `--no-examples` as well.
- **Rate limits.** The script waits `--delay` seconds between requests. If Wiktionary still responds with "too many requests" (HTTP 429), the script waits for the time the server asks for, up to 60 seconds, and retries once. If you see repeated errors, increase `--delay`.
- **Only the first definitions are used.** The script takes the first `--max-defs` definitions in page order, which can include entries such as "plural of …". To get more definitions, raise `-n`.

## Acknowledgements

All definitions and examples come from [Wiktionary](https://www.wiktionary.org/) under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Each card's Source field links back to its page. If you share a deck made with this script, keep that attribution.
