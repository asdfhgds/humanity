from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

SITE_BASE = "/humanity/"


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[tuple[str, int]] = []
        self.fragments: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for attribute in ("href", "src"):
            value = values.get(attribute)
            if value:
                self.references.append((value, self.getpos()[0]))
        for attribute in ("id", "name"):
            value = values.get(attribute)
            if value:
                self.fragments.add(value)

    handle_startendtag = handle_starttag


def resolve_local_path(root: Path, page: Path, url_path: str) -> Path:
    decoded_path = unquote(url_path)
    if decoded_path.startswith(SITE_BASE):
        decoded_path = decoded_path[len(SITE_BASE) :]
    elif decoded_path.startswith("/"):
        decoded_path = decoded_path.lstrip("/")
    else:
        decoded_path = str((page.parent / decoded_path).relative_to(root)).replace("\\", "/")

    target = (root / decoded_path).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError("path escapes build directory")
    if target.is_dir():
        target = target / "index.html"
    elif not target.suffix and (target / "index.html").is_file():
        target = target / "index.html"
    return target


def audit(root: Path) -> list[str]:
    errors: list[str] = []
    pages = sorted(root.rglob("*.html"))
    if not pages:
        return [f"No HTML pages found in {root}"]

    for page in pages:
        parser = PageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        relative_page = page.relative_to(root)

        for reference, line in parser.references:
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc:
                continue
            if not parsed.path and parsed.fragment:
                target = page
            else:
                try:
                    target = resolve_local_path(root, page, parsed.path)
                except ValueError:
                    errors.append(f"{relative_page}:{line}: local path escapes output: {reference}")
                    continue

            if not target.is_file():
                errors.append(f"{relative_page}:{line}: missing local target: {reference}")
                continue

            if parsed.fragment and target.suffix.lower() == ".html":
                target_parser = parser if target == page else PageParser()
                if target != page:
                    target_parser.feed(target.read_text(encoding="utf-8"))
                if unquote(parsed.fragment) not in target_parser.fragments:
                    errors.append(f"{relative_page}:{line}: missing fragment: {reference}")

    return errors


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "web/dist").resolve()
    errors = audit(root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"Static site audit failed with {len(errors)} issue(s).", file=sys.stderr)
        return 1
    print(f"Static site audit passed: {len(list(root.rglob('*.html')))} HTML pages; no missing local links, fragments, or assets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
