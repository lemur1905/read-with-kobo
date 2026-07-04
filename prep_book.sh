#!/bin/bash
# prep_book.sh "title fragment" — prep a book for manual upload to a claude.ai project.
# Finds the kepub in the Kobo iCloud library, converts it with epub2md, reveals the
# .md in Finder (drag it into the project's Files), and copies the project
# instructions to the clipboard (paste into the project's Instructions box).
set -euo pipefail
LIB="${KOBO_LIBRARY:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/Kobo/library}"
cd "$(dirname "$(readlink -f "$0")")"   # resolve symlink so this works from ~/.local/bin

[ $# -ge 1 ] || { echo "usage: $0 \"title fragment\""; exit 1; }
[ -d "$LIB" ] || { echo "library folder not found: $LIB"; echo "set KOBO_LIBRARY to the folder holding your epubs"; exit 1; }

matches=()
while IFS= read -r f; do matches+=("$f"); done \
  < <(find "$LIB" -maxdepth 1 -iname "*$1*" \( -iname "*.epub" \) | sort)

if [ ${#matches[@]} -eq 0 ]; then echo "no match for '$1' in $LIB"; exit 1; fi
if [ ${#matches[@]} -gt 1 ]; then
  echo "multiple matches — be more specific:"
  printf '  %s\n' "${matches[@]##*/}"
  exit 1
fi

convert_out=$(python3 epub2md.py convert "${matches[0]}")
echo "$convert_out"

slug_dir=$(printf '%s\n' "$convert_out" | sed -n 's|^  wrote: \(.*\)/$|\1|p')
[ -n "$slug_dir" ] || { echo "could not parse output dir from converter"; exit 1; }
md=$(ls "$slug_dir"/*.md | grep -v project-instructions)
tail -n +3 "$slug_dir/project-instructions.md" | pbcopy   # strip heading; body only

echo
echo "→ instructions are on the clipboard (paste into the project's Instructions box)"
echo "→ revealing the book file in Finder (drag into the project's Files):"
echo "   $md"
open -R "$md"
