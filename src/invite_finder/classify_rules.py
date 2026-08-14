from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field

from invite_finder.taxonomy import FieldCategory, Industry, RoleType, Seniority

BIG_PHARMA_COMPANIES = {
    "pfizer", "genentech", "roche", "novartis", "merck", "astrazeneca",
    "moderna", "eli lilly", "lilly", "johnson & johnson", "j&j", "sanofi",
    "gsk", "glaxosmithkline", "amgen", "regeneron", "bristol myers squibb",
    "bristol-myers squibb", "abbvie", "biogen", "gilead sciences", "gilead",
    "vertex pharmaceuticals", "takeda", "bayer",
}

VC_FUND_COMPANY_RE = re.compile(r"(ventures|capital|partners|\bvc\b|fund)", re.IGNORECASE)
FOUNDER_RE = re.compile(r"\bco-?founder\b|\bfounder\b|\bceo\b", re.IGNORECASE)
VC_TITLE_RE = re.compile(
    r"\bgeneral partner\b|\bpartner\b|\bprincipal\b|\binvestor\b|"
    r"\bangel investor\b|\bventure capitalist\b",
    re.IGNORECASE,
)
RESEARCH_RE = re.compile(
    r"\bphd\b|\bpost-?doc(toral)?\b|\bprofessor\b|\bresearch scientist\b|"
    r"\bresearch fellow\b",
    re.IGNORECASE,
)
STUDENT_RE = re.compile(
    r"\bstudent\b|\bundergrad(uate)?\b|\bms candidate\b|\bphd candidate\b|"
    r"\bincoming\b|\bnew grad\b",
    re.IGNORECASE,
)
LEADERSHIP_TITLE_RE = re.compile(
    r"\bvp\b|\bvice president\b|\bhead of\b|\bdirector\b|\bchief\b|\bcto\b|"
    r"\bcoo\b|\bcfo\b|\bceo\b|\bfounder\b|\bpartner\b",
    re.IGNORECASE,
)
SENIOR_TITLE_RE = re.compile(r"\bsenior\b|\bstaff\b|\bprincipal\b|\blead\b", re.IGNORECASE)
JUNIOR_TITLE_RE = re.compile(r"\bjunior\b|\bintern\b|\bassociate\b", re.IGNORECASE)


@dataclass
class ClassificationHint:
    """A rules-derived hint, not a final label. Consumed by classify.py as a
    prompt hint, and used directly as the offline fallback when no
    OPENAI_API_KEY is configured."""

    field: FieldCategory | None = None
    role_type: RoleType | None = None
    seniority: Seniority | None = None
    industries: list[Industry] = dc_field(default_factory=list)
    tags: list[str] = dc_field(default_factory=list)


def apply_rules(
    *,
    name: str | None = None,
    headline: str | None = None,
    company: str | None = None,
    bio_short: str | None = None,
) -> ClassificationHint:
    text = " | ".join(filter(None, [name, headline, company, bio_short]))
    headline_text = headline or ""
    company_text = company or ""

    hint = ClassificationHint()

    if STUDENT_RE.search(text):
        hint.role_type = RoleType.STUDENT_EARLY_CAREER
        hint.seniority = Seniority.JUNIOR
    elif FOUNDER_RE.search(headline_text):
        hint.role_type = RoleType.FOUNDER
        hint.seniority = Seniority.LEADERSHIP
    else:
        hint.role_type = RoleType.WORKING_PROFESSIONAL

    if VC_TITLE_RE.search(headline_text) and (
        VC_FUND_COMPANY_RE.search(company_text) or "vc" in headline_text.lower()
    ):
        hint.industries.append(Industry.VC_INVESTOR)
        hint.field = FieldCategory.FINANCE_INVESTING
        hint.seniority = Seniority.LEADERSHIP

    if hint.field is None and RESEARCH_RE.search(text):
        hint.field = FieldCategory.RESEARCH_ACADEMIA

    if hint.seniority is None:
        if LEADERSHIP_TITLE_RE.search(headline_text):
            hint.seniority = Seniority.LEADERSHIP
        elif SENIOR_TITLE_RE.search(headline_text):
            hint.seniority = Seniority.SENIOR
        elif JUNIOR_TITLE_RE.search(headline_text):
            hint.seniority = Seniority.JUNIOR

    company_lower = company_text.lower()
    if any(pharma in company_lower for pharma in BIG_PHARMA_COMPANIES):
        hint.industries.append(Industry.BIOTECH_PHARMA)
        if "big pharma" not in hint.tags:
            hint.tags.append("big pharma")

    return hint


def hint_to_labels(
    hint: ClassificationHint,
    *,
    field: FieldCategory | None = None,
) -> dict:
    """Converts a hint into a complete label set, used as the offline/no-LLM
    fallback. Missing pieces fall back to OTHER/UNKNOWN rather than a guess."""
    return {
        "field": (hint.field or field or FieldCategory.OTHER).value,
        "field_other_label": None,
        "role_type": (hint.role_type or RoleType.WORKING_PROFESSIONAL).value,
        "seniority": (hint.seniority or Seniority.UNKNOWN).value,
        "industries": [i.value for i in hint.industries[:3]],
        "tags": hint.tags[:6],
        "confidence": 0.4 if (hint.field or hint.industries) else 0.2,
    }
