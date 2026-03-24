from app.services.document_ir import extract_text_from_ir
from app.services.document_ir import parse_document_to_ir


def extract_text(file_path: str, content_type: str | None = None) -> str:
    ir = parse_document_to_ir(file_path, content_type)
    return extract_text_from_ir(ir)
