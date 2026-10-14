"""Public NLM journal catalogue; no licensed API or credentials are used."""
from __future__ import annotations
import re

NLM_URL = "https://ftp.ncbi.nlm.nih.gov/pubmed/J_Medline.txt"


def parse_nlm(text: str) -> list[dict]:
    journals = []
    for block in re.split(r"-{20,}", text):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        title = fields.get("JournalTitle", "").strip()
        identifiers = [fields.get("ISSN (Print)", ""), fields.get("ISSN (Online)", "")]
        identifiers = list(dict.fromkeys(value.strip().upper() for value in identifiers
                                        if re.fullmatch(r"\d{4}-\d{3}[\dXx]", value.strip())))
        if title and identifiers:
            journals.append(dict(title=title, issn=identifiers[0], aliases=";".join(identifiers),
                                 category="", jif=None, jif_year=None,
                                 enabled=1, source="NLM（SCI 收录状态未核实）"))
    return journals
