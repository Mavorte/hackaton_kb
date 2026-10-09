"""Cteni PDF: text po strankach, obrazky stranek (pro vizualni model) a podpisy z vektorovych kreseb po strankach."""
import io
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf


@dataclass
class PdfDoc:
    path: Path
    all_texts: list[str] = field(default_factory=list)
    all_sigs: list[int] = field(default_factory=list)  # podpisy (vektorove kresby) po strankach, jen u born-digital
    start: int = 0
    end: int | None = None  # (start, end) = pohled na cast souboru (segment)

    @property
    def _stop(self) -> int:
        return len(self.all_texts) if self.end is None else self.end

    @property
    def page_texts(self) -> list[str]:
        return self.all_texts[self.start:self._stop]

    @property
    def page_count(self) -> int:
        return self._stop - self.start

    @property
    def first_page_text(self) -> str:
        return self.page_texts[0] if self.page_texts else ""

    @property
    def has_text_layer(self) -> bool:
        return any(t.strip() for t in self.page_texts)

    @property
    def signatures(self) -> int:
        return sum(self.all_sigs[self.start:self._stop])

    def sub(self, start: int, end: int) -> "PdfDoc":
        return PdfDoc(self.path, self.all_texts, self.all_sigs, start, end)

    def page_images(self, dpi: int = 100, max_pages: int = 8, autocontrast: bool = False) -> list[bytes]:
        """PNG stran (jen rozsah segmentu). autocontrast zlepsi cteni nekvalitnich skenu."""
        out = []
        with pymupdf.open(self.path) as d:
            for i in range(self.start, min(self._stop, self.start + max_pages)):
                png = d[i].get_pixmap(dpi=dpi).tobytes("png")
                if autocontrast:
                    from PIL import Image, ImageOps

                    buf = io.BytesIO()
                    ImageOps.autocontrast(Image.open(io.BytesIO(png)).convert("L")).save(buf, "PNG")
                    png = buf.getvalue()
                out.append(png)
        return out


def read_pdf(path: str | Path) -> PdfDoc:
    path = Path(path)
    with pymupdf.open(path) as d:
        texts = [p.get_text().strip() for p in d]
        # podpis = vektorova kresba s aspon 2 bezierovymi segmenty (jen born-digital PDF; u skenu pocita vizualni model)
        sigs = [sum(1 for dr in p.get_drawings() if sum(1 for it in dr["items"] if it[0] == "c") >= 2) for p in d]
    return PdfDoc(path, texts, sigs)
