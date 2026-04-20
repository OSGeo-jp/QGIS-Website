#!/usr/bin/env python3
"""Restore corrupted Hugo shortcode names in translated .po files.

When translators translate a paragraph that contains a Hugo shortcode tag
(e.g. {{< rich-box-end >}}), the shortcode *name* can get accidentally
translated too — for example, Swedish "end" = "slut" turns
{{< rich-box-end >}} into {{< rich-box-slut >}}, which breaks Hugo builds.

This script:
  1. Collects all valid shortcode names from the theme's shortcodes directory.
  2. Scans every translated .po file under translations/.
  3. For each msgid/msgstr pair, extracts shortcode invocations in order.
  4. If a shortcode name in msgstr is not a recognised shortcode, replaces it
     with the name from the corresponding position in msgid.
  5. Writes back any modified .po files.

Run this after 'make txpull' and before 'make messages-compile'.
It is called automatically by 'make txpull'.

Usage:
    python3 scripts/i18n/sanitize_shortcodes.py [--translations DIR] [--themes DIR] [--dry-run]
"""
import argparse
import glob
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Hugo shortcode tag pattern — matches both {{< name ... >}} and {{% name %}}
# ---------------------------------------------------------------------------
# Group 1: optional leading /  (closing tag marker)
# Group 2: shortcode name
# Group 3: everything after name inside the delimiters (args / whitespace)
SHORTCODE_RE = re.compile(
    r'(\{\{(?:<|%)[ \t]*)(/?)([A-Za-z0-9_-]+)((?:[^>%]|>(?!\}\})|%(?!\}\}))*?)(?:>|%)(\}\})',
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# .po file message parser
# ---------------------------------------------------------------------------
# Matches a full msgid+msgstr entry including multi-line continuations.
# Returns (full_match, msgid_str, msgstr_str) where *_str is the
# concatenated, still-escaped content (backslash sequences intact).
PO_ENTRY_RE = re.compile(
    r'(msgid\s+"((?:[^"\\]|\\.)*)"\s*(?:"(?:[^"\\]|\\.)*"\s*)*)'  # msgid block
    r'(msgstr\s+"((?:[^"\\]|\\.)*)"\s*(?:"(?:[^"\\]|\\.)*"\s*)*)',  # msgstr block
    re.DOTALL,
)

# Continuation lines inside a msgid/msgstr block
CONTINUATION_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _join_po_string(block: str) -> str:
    """Concatenate all quoted strings in a msgid/msgstr block into one."""
    return ''.join(CONTINUATION_RE.findall(block))


def get_valid_shortcodes(themes_dir: str) -> set[str]:
    """Return every shortcode name found in the theme layouts."""
    names: set[str] = set()
    pattern = os.path.join(themes_dir, '**', 'layouts', 'shortcodes', '*.html')
    for path in glob.glob(pattern, recursive=True):
        names.add(os.path.splitext(os.path.basename(path))[0])
    return names


def list_shortcodes(text: str) -> list[tuple[str, str]]:
    """Return ordered list of (slash, name) for all shortcode tags in text."""
    return [(m.group(2), m.group(3)) for m in SHORTCODE_RE.finditer(text)]


def restore_names(msgid_raw: str, msgstr_raw: str, valid: set[str]) -> tuple[str, list[str]]:
    """Fix invalid shortcode names in msgstr using positions from msgid.

    Returns (new_msgstr_raw, list_of_fixes).  If no changes, new == old.
    """
    id_shortcodes = list_shortcodes(msgid_raw)
    if not id_shortcodes:
        return msgstr_raw, []

    result = msgstr_raw
    fixes: list[str] = []
    offset = 0  # running character offset after previous replacements

    for i, m in enumerate(SHORTCODE_RE.finditer(msgstr_raw)):
        name = m.group(3)
        if name in valid:
            continue
        if i >= len(id_shortcodes):
            continue  # more shortcodes in msgstr than msgid — leave alone
        correct_name = id_shortcodes[i][1]
        if correct_name == name:
            continue

        # Rebuild the shortcode with the correct name, preserving delimiters/args
        old_tag = m.group(0)
        new_tag = m.group(1) + m.group(2) + correct_name + m.group(4) + m.group(5)
        start = m.start() + offset
        end = m.end() + offset
        result = result[:start] + new_tag + result[end:]
        offset += len(new_tag) - len(old_tag)
        fixes.append(f'{name!r} → {correct_name!r}')

    return result, fixes


def process_po_file(path: str, valid: set[str], dry_run: bool) -> int:
    """Process one .po file. Returns number of shortcode names fixed."""
    with open(path, encoding='utf-8') as f:
        content = f.read()

    total_fixes = 0
    new_content = content

    for entry in PO_ENTRY_RE.finditer(content):
        msgid_block = entry.group(1)
        msgstr_block = entry.group(3)

        msgid_raw = _join_po_string(msgid_block)
        msgstr_raw = _join_po_string(msgstr_block)

        if not msgstr_raw.strip():
            continue  # untranslated entry, skip

        new_msgstr_raw, fixes = restore_names(msgid_raw, msgstr_raw, valid)
        if not fixes:
            continue

        total_fixes += len(fixes)
        # Replace only the msgstr part of the matched block.
        # We rebuild the whole msgstr block with the patched string.
        old_msgstr_block = msgstr_block
        # Rebuild as a simple single-line msgstr (po tools will re-wrap as needed)
        escaped = new_msgstr_raw  # already escaped as it came from the file
        new_msgstr_block = f'msgstr "{escaped}"'
        new_content = new_content.replace(old_msgstr_block, new_msgstr_block, 1)

        print(f'  {os.path.basename(path)}: {"; ".join(fixes)}')

    if total_fixes and not dry_run:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)

    return total_fixes


def main() -> None:
    ap = argparse.ArgumentParser(
        description='Restore corrupted Hugo shortcode names in translated .po files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        '--translations',
        default='translations',
        help='Root directory containing language subdirs with .po files (default: %(default)s)',
    )
    ap.add_argument(
        '--themes',
        default='themes',
        help='Root directory of Hugo themes (default: %(default)s)',
    )
    ap.add_argument(
        '--dry-run',
        action='store_true',
        help='Print what would change without modifying files',
    )
    args = ap.parse_args()

    themes_dir = args.themes
    translations_dir = args.translations

    if not os.path.isdir(themes_dir):
        print(f'ERROR: themes directory not found: {themes_dir}', file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(translations_dir):
        print(f'ERROR: translations directory not found: {translations_dir}', file=sys.stderr)
        sys.exit(1)

    valid = get_valid_shortcodes(themes_dir)
    if not valid:
        print('WARNING: no shortcode names found in themes — check --themes path', file=sys.stderr)

    po_files = sorted(glob.glob(os.path.join(translations_dir, '**', '*.po'), recursive=True))
    if not po_files:
        print('No .po files found — nothing to do.')
        return

    total = 0
    for po_file in po_files:
        n = process_po_file(po_file, valid, args.dry_run)
        total += n

    if total:
        action = 'Would fix' if args.dry_run else 'Fixed'
        print(f'{action} {total} corrupted shortcode name(s) across {len(po_files)} file(s).')
    else:
        print('No corrupted shortcode names found.')


if __name__ == '__main__':
    main()
