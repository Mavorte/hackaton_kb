"""Cteni PDF: text po strankach, obrazky stranek (pro vizualni model) a pocet podpisu z vektorovych kreseb."""
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf


@dataclass
class PdfDoc:
    path: Path
    page_texts: list[str] = field(default_factory=list)
    signatures: int = 0

    @property
    def page_count(self) -> int:
        return len(self.page_texts)

    @property
    def first_page_text(self) -> str:
        return self.page_texts[0] if self.page_texts else ""

    @property
    def has_text_layer(self) -> bool:
        return any(t.strip() for t in self.page_texts)

    def page_images(self, dpi: int = 100, max_pages: int = 8) -> list[bytes]:
        with pymupdf.open(self.path) as d:
            return [p.get_pixmap(dpi=dpi).tobytes("png") for p in list(d)[:max_pages]]


def read_pdf(path: str | Path) -> PdfDoc:
    path = Path(path)
    with pymupdf.open(path) as d:
        texts = [p.get_text().strip() for p in d]
        # podpis = vektorova kresba s aspon 2 bezierovymi segmenty (jen u born-digital PDF; u skenu rozhoduje vizualni model)
        sigs = sum(1 for p in d for dr in p.get_drawings() if sum(1 for it in dr["items"] if it[0] == "c") >= 2)
    return PdfDoc(path, texts, sigs)
