"""Playwright scraper for the NS UARB public documents database (FileMaker WebDirect).

The site has no stable element ids, so everything is located by text, CSS class or
on-screen position. See "Site findings" in PLAN.md for the reasoning behind each step.
"""

import logging
import re
import time
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import Browser, Locator, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from .config import settings
from .models import Document, DocType, MatterInfo

log = logging.getLogger(__name__)

TAB_RE = re.compile(r"^(Exhibits|Key Documents|Other Documents|Transcripts|Recordings) - (\d+)$")
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
MATTER_LOAD_TIMEOUT_S = 25
DIALOG_TIMEOUT_MS = 20_000
DOWNLOAD_TIMEOUT_MS = 60_000

# Header label text -> MatterInfo fields for the first and second line beneath it.
HEADER_FIELDS = {
    "Matter No\nStatus": ("matter_number", "status"),
    "Title - Description": ("title", "description"),
    "Type\nCategory": ("type", "category"),
    "Date Received": ("date_received", None),
    "Decision Date": ("date_final_submissions", None),
    "Date Final Submissions": ("date_final_submissions", None),
    "Outcome": ("outcome", None),
}

# Every element that directly holds text, with its position.
_TEXT_ELEMENTS_JS = """() => {
  const out = [];
  for (const e of document.querySelectorAll('body *')) {
    if (![...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    const r = e.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    out.push({text: e.innerText.trim(), x: r.x, y: r.y});
  }
  return out;
}"""


class MatterNotFound(Exception):
    pass


class UarbScraper:
    """One browser shared across matters. Use as a context manager."""

    def __init__(self, headless: bool | None = None, download_dir: Path | None = None):
        self.headless = settings.headless if headless is None else headless
        self.download_dir = download_dir or settings.download_dir
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.page: Page | None = None
        self.matter_number: str | None = None
        self._current_tab: DocType | None = None

    def __enter__(self) -> "UarbScraper":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        context = self._browser.new_context(accept_downloads=True, viewport={"width": 1400, "height": 1000})
        self.page = context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    # --- navigation -------------------------------------------------------

    def open_matter(self, matter_number: str, retries: int = 1) -> None:
        for attempt in range(retries + 1):
            try:
                self._search(matter_number)
                self.matter_number = matter_number
                self._current_tab = None
                return
            except MatterNotFound:
                raise
            except PlaywrightError as e:
                self._screenshot(f"open_{matter_number}")
                if attempt == retries:
                    raise
                log.warning("open_matter(%s) failed (%s), retrying", matter_number, e)

    def _search(self, matter_number: str) -> None:
        page = self.page
        log.info("Opening matter %s", matter_number)
        page.goto(settings.uarb_url, timeout=60_000)
        placeholder = page.get_by_text("eg M01234", exact=True)
        comm_error = page.get_by_text("Communication problem")
        placeholder.or_(comm_error).first.wait_for(timeout=30_000)
        # After a long session WebDirect can show a "Communication problem" page; a reload recovers.
        if comm_error.is_visible():
            log.info("Site reported a communication problem, reloading")
            page.reload(timeout=60_000)
            placeholder.wait_for(timeout=30_000)
        box = placeholder.bounding_box()
        # The editable field is a div overlaid on the placeholder, so click by position.
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_function("document.activeElement && document.activeElement.isContentEditable", timeout=10_000)
        page.keyboard.type(matter_number, delay=50)
        typed = page.evaluate("document.activeElement.innerText").strip()
        if typed != matter_number:
            raise PlaywrightError(f"matter field contains {typed!r}, expected {matter_number!r}")

        for btn in page.locator("button", has_text="Search").all():
            bb = btn.bounding_box()
            if bb and abs(bb["y"] - box["y"]) < 30:
                btn.click()
                break
        else:
            raise PlaywrightError("could not find the Go Directly to Matter search button")

        # Not-found matters silently stay on the landing page, so poll for the tabs.
        deadline = time.monotonic() + MATTER_LOAD_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._tab_locator().count() >= 5 and self._header_value("Matter No\nStatus", 0) == matter_number:
                return
            page.wait_for_timeout(500)
        if page.get_by_text("Go Directly to Matter", exact=True).is_visible():
            raise MatterNotFound(matter_number)
        raise PlaywrightError(f"matter page for {matter_number} did not load")

    # --- metadata ---------------------------------------------------------

    def get_metadata(self) -> MatterInfo:
        header = self._read_header()
        fields: dict = {"matter_number": self.matter_number, "title": ""}
        for label, (first, second) in HEADER_FIELDS.items():
            if label not in header:
                continue
            values = header[label]
            if first and values:
                fields[first] = values[0]
            if second and len(values) > 1:
                fields[second] = " ".join(values[1:])
            if first == "date_final_submissions":
                fields["date_final_label"] = label
        for key in ("date_received", "date_final_submissions"):
            fields[key] = _parse_date(fields.get(key))
        return MatterInfo(**fields, tab_counts=self.get_tab_counts())

    def get_tab_counts(self) -> dict[DocType, int]:
        counts = {}
        for text in self._tab_locator().all_inner_texts():
            if m := TAB_RE.match(text.strip()):
                counts[DocType(m.group(1))] = int(m.group(2))
        return counts

    def _tab_locator(self) -> Locator:
        return self.page.get_by_text(TAB_RE)

    def _read_header(self) -> dict[str, list[str]]:
        """Map each header label to the values below it, top to bottom, by on-screen position."""
        elements = self.page.evaluate(_TEXT_ELEMENTS_JS)
        labels = [e for e in elements if e["text"] in HEADER_FIELDS]
        if not labels:
            return {}
        label_y = min(l["y"] for l in labels)
        tabs_y = min((e["y"] for e in elements if TAB_RE.match(e["text"])), default=label_y + 150)
        labels.sort(key=lambda l: l["x"])
        header: dict[str, list[tuple[float, str]]] = {l["text"]: [] for l in labels}
        for e in elements:
            if not (label_y + 10 < e["y"] < tabs_y) or e["text"] in HEADER_FIELDS or "Back to Search" in e["text"]:
                continue
            # Values start slightly left of their label, hence the tolerance.
            column = [l for l in labels if l["x"] <= e["x"] + 20]
            if column:
                header[column[-1]["text"]].append((e["y"], e["text"]))
        return {k: [t for _, t in sorted(v)] for k, v in header.items()}

    def _header_value(self, label: str, index: int) -> str | None:
        values = self._read_header().get(label, [])
        return values[index] if len(values) > index else None

    # --- documents --------------------------------------------------------

    def list_documents(self, doc_type: DocType, limit: int | None = None) -> list[Document]:
        """Documents on a tab, newest first. Scrolls the lazy list until `limit` (or all) are loaded."""
        total = self._open_tab(doc_type)
        wanted = total if limit is None else min(limit, total)
        docs: dict[str, Document] = {}
        stalls = 0
        while len(docs) < wanted and stalls < 3:
            before = len(docs)
            for doc, _ in self._rendered_rows():
                docs.setdefault(doc.doc_no, doc)
            if len(docs) == before:
                stalls += 1
            if len(docs) < wanted:
                self._scroll_list()
        return list(docs.values())[:wanted]

    def download_documents(
        self, doc_type: DocType, limit: int | None = None, dest_dir: Path | None = None, failed: list[Document] | None = None
    ) -> list[Document]:
        """Returns the downloaded documents; ones that failed are appended to `failed` if given."""
        limit = settings.max_documents if limit is None else limit
        dest_dir = dest_dir or self.download_dir / (self.matter_number or "unknown")
        dest_dir.mkdir(parents=True, exist_ok=True)
        docs = self.list_documents(doc_type, limit)
        downloaded = []
        for doc in docs:
            try:
                doc.path = self._download_one(doc, dest_dir)
                downloaded.append(doc)
                log.info("Downloaded %s -> %s", doc.doc_no, doc.path)
            except PlaywrightError as e:
                log.warning("Skipping %s: %s", doc.doc_no, e)
                self._screenshot(f"download_{doc.doc_no}")
                self._close_dialog()
                if failed is not None:
                    failed.append(doc)
        return downloaded

    def _open_tab(self, doc_type: DocType) -> int:
        tab = self.page.get_by_text(re.compile(rf"^{re.escape(doc_type.value)} - \d+$"))
        count = int(TAB_RE.match(tab.inner_text().strip()).group(2))
        # Re-clicking the active tab pops a "No Matching Records" dialog, so only click when switching.
        if self._current_tab != doc_type:
            tab.click()
            self._current_tab = doc_type
        self._dismiss_alert()
        if count:
            # Not every tab shows "Found Count", so wait for the rows instead (after old ones clear).
            self.page.wait_for_timeout(1000)
            self._go_get_it().first.wait_for(timeout=30_000)
            self._scroll_to_top()
        return count

    def _go_get_it(self) -> Locator:
        return self.page.locator("button", has_text="GO GET IT")

    def _rendered_rows(self) -> list[tuple[Document, Locator]]:
        rows = []
        for btn in self._go_get_it().all():
            lines = [l.strip() for l in btn.locator("xpath=ancestor::*[3]").inner_text().splitlines() if l.strip()]
            fields = lines[: lines.index("Preview")] if "Preview" in lines else lines
            if not fields:
                continue
            # Field order varies by tab:
            #   Other Documents:         doc_no, title, date, security
            #   Exhibits/Key Documents:  title, date, security, doc_no
            date_idx = next((i for i, l in enumerate(fields) if DATE_RE.match(l)), None)
            if date_idx == 1:
                title, doc_no = fields[0], fields[3] if len(fields) > 3 else fields[0]
            else:
                doc_no, title = fields[0], fields[1] if len(fields) > 1 and fields[1] != fields[date_idx or 0] else ""
            doc_date = fields[date_idx] if date_idx is not None else None
            rows.append((Document(doc_no=doc_no, title=title, date=doc_date), btn))
        return rows

    def _scroll_list(self, to_top: bool = False) -> None:
        """Scroll the lazy document list down a page (or back to the top); new rows render after a moment."""
        # The list is a virtualised Vaadin grid: only mouse-wheel events over it scroll it.
        grid = self.page.locator(".v-grid-tablewrapper", has=self._go_get_it())
        bb = grid.first.bounding_box() if grid.count() else None
        if bb:
            self.page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            self.page.mouse.wheel(0, -50_000 if to_top else 600)
        self.page.wait_for_timeout(1500)

    def _scroll_to_top(self) -> None:
        self._scroll_list(to_top=True)

    def _find_button(self, doc_no: str) -> Locator:
        for _ in range(10):
            for doc, btn in self._rendered_rows():
                if doc.doc_no == doc_no:
                    return btn
            self._scroll_list()
        raise PlaywrightError(f"row for document {doc_no} not found")

    def _download_one(self, doc: Document, dest_dir: Path) -> Path:
        page = self.page
        self._find_button(doc.doc_no).click()
        file_btn = page.locator(".v-window .fm-download-button").first
        file_btn.wait_for(timeout=DIALOG_TIMEOUT_MS)
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as info:
            file_btn.click()
        download = info.value
        path = _unique_path(dest_dir / download.suggested_filename)
        download.save_as(path)
        self._close_dialog()
        return path

    def _dismiss_alert(self) -> None:
        ok = self.page.locator(".v-window [role=button]", has_text=re.compile(r"^\s*OK\s*$"))
        if ok.count():
            log.info("Dismissing site dialog: %s", self.page.locator(".v-window-header").first.inner_text())
            ok.first.click()
            self.page.wait_for_timeout(1000)

    def _close_dialog(self) -> None:
        close = self.page.locator(".v-window [role=button]", has_text="Close")
        try:
            if close.count():
                close.first.click()
                self.page.locator(".v-window").first.wait_for(state="hidden", timeout=10_000)
        except PlaywrightError as e:
            log.warning("Could not close download dialog: %s", e)

    def _screenshot(self, name: str) -> None:
        try:
            errors = self.download_dir / "errors"
            errors.mkdir(parents=True, exist_ok=True)
            path = errors / f"{datetime.now():%Y%m%d-%H%M%S}_{name}.png"
            self.page.screenshot(path=path)
            log.info("Saved error screenshot %s", path)
        except PlaywrightError:
            pass


def _parse_date(value: str | None) -> date | None:
    if value and DATE_RE.match(value):
        return datetime.strptime(value, "%m/%d/%Y").date()
    return None


def _unique_path(path: Path) -> Path:
    n = 1
    candidate = path
    while candidate.exists():
        candidate = path.with_stem(f"{path.stem}_{n}")
        n += 1
    return candidate
