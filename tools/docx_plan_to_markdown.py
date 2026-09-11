from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


ROOT = Path(r"C:\Users\Daniel.Mhango\Documents\KRE_CATASTROPHE_MODEL")
SOURCE = ROOT / "deliverables" / "Klapton Re Earthquake Catastrophe Modelling Platform Build Plan.docx"
TARGET = ROOT / "deliverables" / "Klapton Re Earthquake Catastrophe Modelling Platform Build Plan.md"


def iter_blocks(document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def paragraph_markdown(paragraph):
    parts = []
    for child in paragraph._p.iterchildren():
        if child.tag == qn("w:r"):
            parts.append("".join(node.text or "" for node in child.iter(qn("w:t"))))
        elif child.tag == qn("w:hyperlink"):
            label = "".join(node.text or "" for node in child.iter(qn("w:t")))
            rel_id = child.get(qn("r:id"))
            if rel_id and rel_id in paragraph.part.rels:
                parts.append(f"[{label}]({paragraph.part.rels[rel_id].target_ref})")
            else:
                parts.append(label)
    text = "".join(parts).strip()
    if not text:
        return None

    style = paragraph.style.name if paragraph.style else ""
    if style == "Title":
        return f"# {text}"
    if style == "Subtitle":
        return f"*{text}*"
    if style == "Heading 1":
        return f"## {text}"
    if style == "Heading 2":
        return f"### {text}"
    if style == "Heading 3":
        return f"#### {text}"
    if style.startswith("List Bullet"):
        indent = "  " if style.endswith("2") else ""
        return f"{indent}- {text}"
    if style.startswith("List Number"):
        return f"1. {text}"
    if text.isupper() and len(text) < 80:
        return f"**{text.title()}**"
    return text


def clean_cell(cell):
    text = "<br>".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
    return text.replace("|", "\\|").replace("\n", "<br>")


def table_markdown(table):
    rows = [[clean_cell(cell) for cell in row.cells] for row in table.rows]
    if not rows:
        return []
    headers = rows[0]
    result = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows[1:]:
        result.append("| " + " | ".join(row) + " |")
    return result


def main():
    doc = Document(SOURCE)
    lines = [
        "![Klapton Reinsurance PLC](../assets/kre-logo.png)",
        "",
    ]
    for block in iter_blocks(doc):
        if isinstance(block, Paragraph):
            rendered = paragraph_markdown(block)
            if rendered:
                lines.extend([rendered, ""])
        else:
            rendered = table_markdown(block)
            if rendered:
                lines.extend(rendered)
                lines.append("")

    header = [
        "<!--",
        "KRE brand: primary #234A9E, white #FFFFFF, neutral grey.",
        "This plan is the product and engineering baseline. Model assumptions remain subject to the formal scientific gates defined below.",
        "-->",
        "",
    ]
    TARGET.write_text("\n".join(header + lines).rstrip() + "\n", encoding="utf-8")
    print(TARGET)


if __name__ == "__main__":
    main()
