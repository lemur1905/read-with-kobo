# read-with-kobo

Ask Claude questions about the book you're reading on a Kobo, from your phone, mid-chapter, without hand-feeding it context.

You say something like "I'm at 43%. What does Melville mean by the pasteboard mask?" or "page 237 of 955, why is Franz anguished here?" and Claude answers grounded in the passage you're actually looking at.

## How it works

An e-reader can't tell an assistant where you are in a book, but it does display a position as a percent or a page count. This tool converts an epub into markdown with position markers woven through the text.

```
# CHAPTER 36 The Quarter-Deck · [28%–28%]
...
— CHAPTER 36 The Quarter-Deck · [28%] —
```

A breadcrumb line every ~400 words carries the chapter title and the percent through the book. The percent is computed character-weighted over the full epub spine, front matter included, which closely tracks how Kobo computes kepub progress. Measured drift on real devices stayed under 2.5 percentage points, usually under 1.

You upload the converted file to a [Claude project](https://claude.com/), one project per book, together with generated project instructions. The instructions teach Claude to convert "page X of Y" into a percent, to search the book text for the literal position marker, to match dictated and typo-ridden quotes generously, and to echo back the resolved chapter so a stale page count gets caught immediately.

The instructions also tell Claude to treat everything past your position as unread, and to lift that guard if you say spoilers are fine. Treat this as mitigation, not a guarantee. Language models sometimes spoil even when told not to, especially on long books where retrieval can surface a later passage. Reader beware.

Books that fit in Claude's context window get exact lookup. Bigger books work through project knowledge search (RAG), which is what the dense self-locating breadcrumbs are for, since every retrieved chunk carries its own position on its face.

## Installation

Requires Python 3.8 or newer. No dependencies beyond the standard library.

```sh
git clone https://github.com/lemur1905/read-with-kobo.git
cd read-with-kobo
```

Run the commands below from this folder. If you want `prep_book` available everywhere, symlink it onto your PATH:

```sh
ln -s "$(pwd)/prep_book.sh" ~/.local/bin/prep_book
```

Optional environment variables, worth putting in your shell profile:

```sh
export KOBO_LIBRARY=~/path/to/your/epubs   # where sync and prep_book look for books
export READER_NAME=YourName                # personalizes the generated instructions
```

## Usage

```sh
# one book -> out/<slug>/<slug>.md plus project-instructions.md
python3 epub2md.py convert path/to/book.kepub.epub

# convert every book in your library that isn't converted yet
python3 epub2md.py sync --library ~/path/to/your/epubs

# sanity-check the percent mapping against your Kobo's own database
python3 epub2md.py calibrate book.kepub.epub --db KoboReader.sqlite
```

Then on claude.ai, create a project named after the book, paste `project-instructions.md` into the project's Instructions, and upload the book's `.md` as project knowledge.

`prep_book.sh` wraps the per-book flow on macOS. It finds the epub in your library by a title fragment, converts it, reveals the `.md` in Finder for drag-and-drop, and puts the instructions on your clipboard.

The generated instructions refer to "the user" by default. Set `READER_NAME` (or pass `--reader`) if you want them to use your name.

## Notes

- Works best with kepubs, Kobo's own format (see [kepubify](https://pgaskin.net/kepubify/)), since kepub reading progress is character-based like the markers. Plain epubs work too, within a slightly wider tolerance.
- `calibrate` reads the sentence-level position Kobo itself recorded in `KoboReader.sqlite` and compares it with the computed percent. Useful for checking a new device or a different edition.
- When dictating a position, say the page and the total together, as in "143 of 412". It's self-contained, and Claude does the division.

## Credits

Developed by Ian Kahn, using [Claude Code](https://claude.com/claude-code).

## License

MIT, see [LICENSE](LICENSE).
