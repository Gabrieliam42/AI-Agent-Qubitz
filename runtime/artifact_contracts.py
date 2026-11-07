"""Format-aware diagnostics; never remove or rewrite generated content."""
from __future__ import annotations

import re
from html.parser import HTMLParser


def html_envelope_issues(source: str) -> list[str]:
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.at_root = False
            self.open_raw_text = None
            self.issues = []

        def handle_starttag(self, tag, _attrs):
            self.at_root = tag == "html"
            if tag in {"script", "style"}:
                self.open_raw_text = tag

        def handle_endtag(self, tag):
            self.at_root = False
            if tag == self.open_raw_text:
                self.open_raw_text = None

        def handle_data(self, text):
            if self.at_root and re.match(r'\s*"\s*,\s*"complete"\s*:\s*(?:true|false)\s*}', text):
                self.issues.append("Staged JSON envelope metadata appears as text directly after the html tag")

    parser = Parser()
    parser.feed(source)
    parser.close()
    if parser.open_raw_text is not None:
        parser.issues.append(f"Unclosed {parser.open_raw_text} element at end of HTML")
    return parser.issues
