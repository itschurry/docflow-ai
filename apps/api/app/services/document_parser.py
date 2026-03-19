import subprocess
import sys
from pathlib import Path

# hwp5proc may live inside the active virtualenv's bin/ directory.
# Resolve its absolute path so subprocess can find it without relying on PATH.
_VENV_BIN = Path(sys.executable).parent
_HWP5PROC = str(_VENV_BIN / "hwp5proc")


def extract_text(file_path: str, content_type: str | None = None) -> str:
    suffix = Path(file_path).suffix.lower()

    if suffix in {".txt", ".md", ".csv", ".json"}:
        return Path(file_path).read_text(encoding="utf-8", errors="ignore")

    if suffix == ".pdf":
        try:
            import fitz

            doc = fitz.open(file_path)
            text_chunks = [page.get_text("text") for page in doc]
            doc.close()
            return "\n".join(text_chunks).strip()
        except Exception:
            return ""

    if suffix == ".docx":
        try:
            from docx import Document

            doc = Document(file_path)
            lines = [p.text for p in doc.paragraphs if p.text]
            return "\n".join(lines).strip()
        except Exception:
            return ""

    if suffix == ".hwp":
        try:
            raw = subprocess.check_output(
                [_HWP5PROC, "cat", str(file_path), "PrvText"],
                timeout=60,
                stderr=subprocess.DEVNULL,
            )
            text = subprocess.check_output(
                ["iconv", "-f", "UTF-16LE", "-t", "UTF-8"],
                input=raw,
                timeout=15,
            ).decode("utf-8", errors="ignore")
            return " ".join(text.split())
        except Exception:
            return ""

    return ""
