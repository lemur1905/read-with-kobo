#!/usr/bin/env python3
"""epub2md — convert a (k)epub into position-marked markdown for a claude.ai project.

convert:   emit <out>/<slug>/<slug>.md (one combined file; --per-chapter for one
           file per chapter) with a breadcrumb line every ~400 words carrying
           chapter title + integer book-percent, plus a project-instructions
           template and a stats summary.
calibrate: compare this script's char-weighted percent against Kobo's own
           ___PercentRead using a KoboReader.sqlite backup.

Percent is computed character-weighted over the full spine (front matter
included), approximating how Kobo computes kepub progress.
"""
import argparse
import math
import os
import posixpath
import re
import sys
import unicodedata
import zipfile
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree as ET


# ---------- epub reading ----------

class Chapter:
    def __init__(self, zip_path):
        self.zip_path = zip_path      # full path inside the zip
        self.title = None             # from TOC, else first heading, else filename
        self.paras = []               # normalized paragraph strings
        self.span_offsets = {}        # koboSpan id -> content-char offset within chapter
        self.char_start = 0           # global content-char offset of chapter start

    @property
    def nchars(self):
        return sum(len(p) for p in self.paras)


class TextExtractor(HTMLParser):
    SKIP = {"style", "script", "head", "title"}
    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "tr",
             "td", "th", "section", "article", "blockquote", "figure",
             "figcaption", "header", "footer", "hr", "aside", "dt", "dd"}
    HEADINGS = {"h1", "h2", "h3"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras = []
        self.buf = []
        self.skip_depth = 0
        self.heading_depth = 0
        self.first_heading = None
        self.heading_buf = []
        self.span_offsets = {}
        self.chars_flushed = 0

    def _norm(self, s):
        return re.sub(r"\s+", " ", s).strip()

    def _flush(self):
        text = self._norm("".join(self.buf))
        self.buf = []
        if text:
            self.paras.append(text)
            self.chars_flushed += len(text)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if tag in self.BLOCK:
            self._flush()
        if tag in self.HEADINGS and self.first_heading is None:
            self.heading_depth += 1
        if tag == "span":
            a = dict(attrs)
            sid = a.get("id", "")
            if sid.startswith("kobo."):
                offset = self.chars_flushed + len(self._norm("".join(self.buf)))
                self.span_offsets[sid] = offset

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in self.HEADINGS and self.heading_depth:
            self.heading_depth -= 1
            if self.heading_depth == 0 and self.first_heading is None:
                h = self._norm("".join(self.heading_buf))
                if h:
                    self.first_heading = h
        if tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self.skip_depth:
            return
        self.buf.append(data)
        if self.heading_depth:
            self.heading_buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _resolve(base_dir, href):
    return posixpath.normpath(posixpath.join(base_dir, unquote(href.split("#")[0])))


def load_book(epub_path):
    z = zipfile.ZipFile(epub_path)
    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = container.find(".//{*}rootfile").get("full-path")
    opf_dir = posixpath.dirname(opf_path)
    opf = ET.fromstring(z.read(opf_path))

    title_el = opf.find(".//{*}metadata/{*}title")
    author_el = opf.find(".//{*}metadata/{*}creator")
    title = title_el.text.strip() if title_el is not None and title_el.text else "Untitled"
    author = author_el.text.strip() if author_el is not None and author_el.text else ""

    manifest = {}   # id -> (zip_path, media_type, properties)
    for item in opf.findall(".//{*}manifest/{*}item"):
        manifest[item.get("id")] = (
            _resolve(opf_dir, item.get("href")),
            item.get("media-type", ""),
            item.get("properties", "") or "",
        )

    spine_paths = []
    spine_el = opf.find(".//{*}spine")
    for ref in spine_el.findall("{*}itemref"):
        if ref.get("linear", "yes") == "no":
            continue
        entry = manifest.get(ref.get("idref"))
        if entry and ("html" in entry[1] or "xml" in entry[1]):
            spine_paths.append(entry[0])

    # TOC: epub3 nav first, then NCX. Map zip_path -> first title seen.
    toc = {}

    def add(path, text):
        text = re.sub(r"\s+", " ", text).strip()
        if text and path not in toc:
            toc[path] = text

    nav_entry = next((m for m in manifest.values() if "nav" in m[2]), None)
    if nav_entry:
        try:
            nav = ET.fromstring(z.read(nav_entry[0]))
            nav_dir = posixpath.dirname(nav_entry[0])
            for a in nav.findall(".//{*}a"):
                href = a.get("href")
                if href:
                    add(_resolve(nav_dir, href), "".join(a.itertext()))
        except Exception:
            pass
    ncx_entry = next((m for m in manifest.values() if "dtbncx" in m[1]), None)
    if ncx_entry:
        try:
            ncx = ET.fromstring(z.read(ncx_entry[0]))
            ncx_dir = posixpath.dirname(ncx_entry[0])
            for np in ncx.findall(".//{*}navPoint"):
                label = np.find("./{*}navLabel/{*}text")
                content = np.find("./{*}content")
                if label is not None and content is not None and label.text:
                    add(_resolve(ncx_dir, content.get("src")), label.text)
        except Exception:
            pass

    chapters = []
    offset = 0
    for path in spine_paths:
        ex = TextExtractor()
        try:
            ex.feed(z.read(path).decode("utf-8", errors="replace"))
            ex.close()
        except Exception as e:
            print(f"  warning: failed to parse {path}: {e}", file=sys.stderr)
        ch = Chapter(path)
        ch.paras = ex.paras
        ch.span_offsets = ex.span_offsets
        ch.char_start = offset
        stem = posixpath.splitext(posixpath.basename(path))[0]
        ch.title = None
        ch.toc_title = toc.get(path)
        ch.fallback = ex.first_heading or stem.replace("_", " ")
        offset += ch.nchars
        chapters.append(ch)

    # Titles: TOC entry when present; a chapter split across several spine
    # files gets "(cont.)" on the continuation files; bare-number entries
    # (numbered sub-chapters) get prefixed with the enclosing part/chapter.
    carry = None
    for ch in chapters:
        t = ch.toc_title
        if t is None:
            ch.title = f"{carry} (cont.)" if carry else ch.fallback
        elif re.fullmatch(r"\d{1,3}", t) and carry:
            ch.title = f"{carry} · §{t}"
        else:
            ch.title = t
            carry = t

    return {"title": title, "author": author, "chapters": chapters, "total_chars": offset}


# ---------- convert ----------

def slugify(s, maxlen=48):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:maxlen].rstrip("-") or "book"


def shorten(title, maxlen=44):
    return title if len(title) <= maxlen else title[: maxlen - 1].rstrip() + "…"


def render_chapter(ch, book_total, marker_every):
    """Markdown for one chapter: header with % range, breadcrumb every ~N words."""
    if not ch.paras:
        return None
    p0 = math.floor(ch.char_start / book_total * 100)
    p1 = math.floor((ch.char_start + ch.nchars) / book_total * 100)
    lines = [f"# {ch.title} · [{p0}%–{p1}%]", ""]
    words_since = 0
    chars_done = 0
    for para in ch.paras:
        lines.append(para)
        lines.append("")
        chars_done += len(para)
        words_since += len(para.split())
        if words_since >= marker_every:
            pct = math.floor((ch.char_start + chars_done) / book_total * 100)
            lines.append(f"— {shorten(ch.title)} · [{pct}%] —")
            lines.append("")
            words_since = 0
    return "\n".join(lines).rstrip() + "\n", p0, p1


INSTRUCTIONS_TEMPLATE = """\
# Project instructions — {title}

{reader} is reading **{title}**{by} on their Kobo and asks questions by voice \
dictation from their phone, usually about the passage they just read.

## Locating their position
- They state where they are as a **percent** ("I'm at 43%"), a **page of \
total** ("page 143 of 412" → 143/412 ≈ 35%), or occasionally chapter + rough \
position. Convert page/total to percent by division.
- If they state a page total ("the book is 412 pages right now"), **remember \
it across conversations** and reuse it when they give a bare page number. The \
total changes only when they change font size, which is rare.
- The book text in project knowledge carries markers like `[43%]` on breadcrumb \
lines and chapter headers with percent ranges like `[55%–58%]`. Given a \
position, **search project knowledge for the literal marker** (e.g. `[43%]`) \
and read the surrounding text before answering. Treat any stated position as \
±2%.
- If their question contains a quote or distinctive phrase, search for that \
phrase — it locates the passage more precisely than any percent.
- Locate the passage silently. Do not narrate the reader's position, restate \
the page math, or confirm where they are, at the start or the end of an \
answer. Mention position only when something seems off, such as a quote \
pointing somewhere other than the stated position, a page total that looks \
stale, or a passage you cannot find. Keep any such note to one short \
sentence.

## Spoilers
- Treat everything past the reader's current position as unread. Do not \
reveal or allude to later events, character fates, twists, or the \
significance a detail takes on later. No hinting ("you'll see", "this \
becomes important").
- Be conservative about the ceiling. A stated percent or page means they \
have just reached that point, so assume the start of that percent, not the \
end. If a quoted phrase sits later in the book than the stated position, \
treat the quote's location as the ceiling instead.
- If the reader waives protection ("spoilers ok", "you can spoil it", "I've \
read this before"), answer freely from the whole book for the rest of that \
conversation, until they say otherwise.

## Answering
- Questions arrive via dictation: expect misspelled names, missing \
punctuation, and slightly garbled quotes. Match generously.
- Ground answers in the text around their position; quote short phrases from \
the book when helpful.
"""


def convert_book(epub_path, out_root, marker_every=400, per_chapter=False,
                 reader=None):
    reader = reader or os.environ.get("READER_NAME", "The user")
    book = load_book(epub_path)
    total = book["total_chars"]
    if not total:
        raise ValueError("no text extracted — is this a valid epub?")
    slug = slugify(book["title"])
    outdir = out_root / slug
    outdir.mkdir(parents=True, exist_ok=True)

    rendered = []
    for i, ch in enumerate(book["chapters"]):
        r = render_chapter(ch, total, marker_every)
        if r:
            rendered.append((i, ch, *r))

    if per_chapter:
        for i, ch, md, p0, p1 in rendered:
            name = f"{i:03d}-{slugify(ch.title, 40)}__{p0:02d}-{p1:02d}pct.md"
            (outdir / name).write_text(md, encoding="utf-8")
    else:
        head = f"# {book['title']}{' — ' + book['author'] if book['author'] else ''}\n\n"
        head += ("Position markers: breadcrumb lines like `— Chapter · [43%] —` mark "
                 "percent-through-book; chapter headers carry their percent range.\n\n")
        combined = head + "\n\n".join(md for _, _, md, _, _ in rendered)
        (outdir / f"{slug}.md").write_text(combined, encoding="utf-8")

    by = f" by {book['author']}" if book["author"] else ""
    (outdir / "project-instructions.md").write_text(
        INSTRUCTIONS_TEMPLATE.format(title=book["title"], by=by, reader=reader),
        encoding="utf-8")

    words = sum(len(p.split()) for ch in book["chapters"] for p in ch.paras)
    est_tokens = total // 4
    regime = ("fits in context" if est_tokens < 160_000 else "RAG mode")
    return {"title": book["title"], "author": book["author"], "slug": slug,
            "outdir": outdir, "chapters": len(rendered), "chars": total,
            "words": words, "est_tokens": est_tokens, "regime": regime}


def cmd_convert(args):
    try:
        r = convert_book(args.epub, args.out, args.marker_every, args.per_chapter,
                         reader=args.reader)
    except ValueError as e:
        sys.exit(str(e))
    by = f" by {r['author']}" if r["author"] else ""
    print(f"{r['title']}{by}")
    print(f"  chapters (spine files with text): {r['chapters']}")
    print(f"  content chars: {r['chars']:,}   words: {r['words']:,}   est. tokens: {r['est_tokens']:,}")
    print(f"  regime: {r['regime']}")
    print(f"  wrote: {r['outdir']}/")


DEFAULT_LIBRARY = ("~/Library/Mobile Documents/com~apple~CloudDocs/Kobo/library")


def cmd_sync(args):
    import json
    lib = args.library.expanduser()
    if not lib.is_dir():
        sys.exit(f"library folder not found: {lib}\n"
                 f"Point me at your epubs with --library <dir> "
                 f"or the KOBO_LIBRARY environment variable.")
    manifest_path = args.out / ".sync-manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    converted, failed, skipped = [], [], 0
    for p in sorted(lib.glob("*.epub")):
        ent = manifest.get(p.name)
        mtime = p.stat().st_mtime
        if ent and ent["mtime"] == mtime and \
                (args.out / ent["slug"] / f"{ent['slug']}.md").exists():
            skipped += 1
            continue
        try:
            r = convert_book(p, args.out, args.marker_every, reader=args.reader)
        except Exception as e:
            failed.append((p.name, str(e)))
            print(f"  FAILED {p.name}: {e}", file=sys.stderr)
            continue
        manifest[p.name] = {"slug": r["slug"], "mtime": mtime,
                            "est_tokens": r["est_tokens"]}
        converted.append(r)
        print(f"  {r['title']}  ({r['est_tokens']:,} tok, {r['regime']})")
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=1))
    print(f"\nsync: {len(converted)} converted, {skipped} already done, "
          f"{len(failed)} failed")
    if failed:
        print("failed files:")
        for name, err in failed:
            print(f"  {name}: {err}")


# ---------- calibrate ----------

def cmd_calibrate(args):
    import sqlite3
    book = load_book(args.epub)
    total = book["total_chars"]
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    q = ("SELECT Title, ___PercentRead, ChapterIDBookmarked FROM content "
         "WHERE ContentType=6 AND ChapterIDBookmarked IS NOT NULL "
         "AND ChapterIDBookmarked != ''")
    rows = con.execute(q).fetchall()
    con.close()

    want = (args.title or book["title"]).lower()[:20]
    matches = [r for r in rows if want in r[0].lower()]
    if not matches:
        sys.exit(f"no in-progress book in the db matches {want!r}. "
                 f"Titles with bookmarks: {[r[0] for r in rows]}")

    for db_title, kobo_pct, bookmark in matches:
        if "#" not in bookmark:
            print(f"{db_title}: bookmark {bookmark!r} has no span anchor, skipping")
            continue
        path, frag = bookmark.split("#", 1)
        path = posixpath.normpath(unquote(path))
        ch = next((c for c in book["chapters"] if c.zip_path.endswith(path)), None)
        if ch is None:
            print(f"{db_title}: bookmark file {path!r} not found in this epub's spine "
                  f"(wrong edition?)")
            continue
        offset = ch.span_offsets.get(frag)
        if offset is None:
            print(f"{db_title}: span {frag!r} not found in {path} "
                  f"(spans present: {len(ch.span_offsets)})")
            continue
        ours = (ch.char_start + offset) / total * 100
        print(f"{db_title}")
        print(f"  Kobo says:   {kobo_pct}%   (bookmark {bookmark})")
        print(f"  we compute:  {ours:.2f}%   (chapter '{ch.title}')")
        print(f"  drift:       {ours - kobo_pct:+.2f} points")


def main():
    import pathlib
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="epub -> position-marked markdown")
    c.add_argument("epub", type=pathlib.Path)
    c.add_argument("-o", "--out", type=pathlib.Path, default=pathlib.Path("out"))
    c.add_argument("--per-chapter", action="store_true",
                   help="one file per chapter instead of one combined file")
    c.add_argument("--marker-every", type=int, default=400,
                   help="words between breadcrumb markers (default 400)")
    c.add_argument("--reader", default=None,
                   help="name used in the generated project instructions "
                        "(default: $READER_NAME or 'The user')")
    c.set_defaults(func=cmd_convert)

    y = sub.add_parser("sync", help="convert every library kepub not yet converted")
    y.add_argument("--library", type=pathlib.Path,
                   default=pathlib.Path(os.environ.get("KOBO_LIBRARY", DEFAULT_LIBRARY)))
    y.add_argument("-o", "--out", type=pathlib.Path, default=pathlib.Path("out"))
    y.add_argument("--marker-every", type=int, default=400)
    y.add_argument("--reader", default=None)
    y.set_defaults(func=cmd_sync)

    k = sub.add_parser("calibrate", help="compare our %% against KoboReader.sqlite")
    k.add_argument("epub", type=pathlib.Path)
    k.add_argument("--db", type=pathlib.Path, required=True)
    k.add_argument("--title", help="title substring to match in the db "
                                   "(default: epub's own title)")
    k.set_defaults(func=cmd_calibrate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
