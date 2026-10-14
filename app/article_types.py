"""Publication-type mappings shared by online queries and offline result views."""
import json
ARTICLE_TYPES = [
    ("全部文章类型", "all"), ("Research Article · 研究论文", "research"),
    ("Review · 综述", "review"), ("Systematic Review · 系统综述", "systematic_review"),
    ("Meta-Analysis · 荟萃分析", "meta_analysis"), ("Clinical Trial · 临床试验", "clinical_trial"),
    ("Case Reports · 病例报告", "case_reports"), ("Editorial · 社论", "editorial"), ("Letter · 来信", "letter"),
]
TYPE_TERMS = {"review": "Review", "systematic_review": "Systematic Review", "meta_analysis": "Meta-Analysis",
              "clinical_trial": "Clinical Trial", "case_reports": "Case Reports", "editorial": "Editorial", "letter": "Letter"}
RESEARCH_EXCLUSIONS = ("Review", "Systematic Review", "Meta-Analysis", "Editorial", "Letter", "Comment", "News")


def normalize_types(value) -> list[str]:
    """Read legacy single values and current JSON arrays without changing old profiles."""
    if isinstance(value, str):
        value = json.loads(value) if value.startswith("[") else [value]
    values = list(dict.fromkeys(value or ["all"]))
    if any(v not in {k for _, k in ARTICLE_TYPES} for v in values):
        raise ValueError("未知文章类型，请重新编辑方案。")
    return ["all"] if "all" in values else values


def type_clause(kind, source: str) -> str:
    selected = normalize_types(kind)
    if selected == ["all"]:
        return ""
    clauses = [_single_clause(k, source) for k in selected]
    return clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"


def _single_clause(kind: str, source: str) -> str:
    if kind == "all":
        return ""
    if kind not in {value for _, value in ARTICLE_TYPES}:
        raise ValueError("未知文章类型，请重新编辑方案。")
    def field(term):
        return f'"{term}"[Publication Type]' if source == "PubMed" else f'PUB_TYPE:"{term.lower()}"'
    if kind == "research":
        return f'({field("Journal Article")} NOT (' + " OR ".join(field(t) for t in RESEARCH_EXCLUSIONS) + "))"
    return field(TYPE_TERMS[kind])


def matches_type(types: list[str], kind) -> bool:
    return any(_matches_single(types, k) for k in normalize_types(kind))


def _matches_single(types: list[str], kind: str) -> bool:
    if kind == "all":
        return True
    lowered = {str(t).lower().replace("_", " ") for t in types}
    if kind == "research":
        return bool(lowered & {"journal article", "research article"}) and not lowered.intersection(t.lower() for t in RESEARCH_EXCLUSIONS)
    if kind == "clinical_trial":
        return any("trial" in t for t in lowered)
    return TYPE_TERMS.get(kind, "").lower() in lowered
