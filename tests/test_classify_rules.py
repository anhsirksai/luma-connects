from __future__ import annotations

import pytest

from invite_finder.classify_rules import apply_rules, hint_to_labels
from invite_finder.taxonomy import FieldCategory, Industry, RoleType, Seniority


@pytest.mark.parametrize(
    "headline,company,expected_role,expected_seniority,expected_industry,expected_field",
    [
        (
            "Partner at Foo Ventures",
            "Foo Ventures",
            RoleType.WORKING_PROFESSIONAL,
            Seniority.LEADERSHIP,
            Industry.VC_INVESTOR,
            FieldCategory.FINANCE_INVESTING,
        ),
        (
            "Principal Scientist, Genentech",
            "Genentech",
            RoleType.WORKING_PROFESSIONAL,
            None,
            Industry.BIOTECH_PHARMA,
            None,
        ),
        (
            "Co-founder & CEO",
            "Nimbus Robotics",
            RoleType.FOUNDER,
            Seniority.LEADERSHIP,
            None,
            None,
        ),
        (
            "PhD candidate, Robot Learning",
            "Stanford University",
            RoleType.STUDENT_EARLY_CAREER,
            Seniority.JUNIOR,
            None,
            None,
        ),
        (
            "Senior Software Engineer",
            "Nimbus Robotics",
            RoleType.WORKING_PROFESSIONAL,
            Seniority.SENIOR,
            None,
            None,
        ),
    ],
)
def test_apply_rules_table(
    headline, company, expected_role, expected_seniority, expected_industry, expected_field
) -> None:
    hint = apply_rules(name="Test Person", headline=headline, company=company, bio_short=None)

    assert hint.role_type == expected_role
    if expected_seniority is not None:
        assert hint.seniority == expected_seniority
    if expected_industry is not None:
        assert expected_industry in hint.industries
    if expected_field is not None:
        assert hint.field == expected_field


def test_big_pharma_tag_is_applied() -> None:
    hint = apply_rules(name="Grace Chen", headline="Principal Scientist", company="Genentech")
    assert "big pharma" in hint.tags
    assert Industry.BIOTECH_PHARMA in hint.industries


def test_hint_to_labels_falls_back_to_other_and_unknown() -> None:
    hint = apply_rules(name="Anon Person", headline=None, company=None, bio_short=None)
    labels = hint_to_labels(hint)
    assert labels["field"] == FieldCategory.OTHER.value
    assert labels["seniority"] == Seniority.UNKNOWN.value
    assert labels["role_type"] == RoleType.WORKING_PROFESSIONAL.value
    assert labels["industries"] == []
    assert 0.0 <= labels["confidence"] <= 1.0


def test_hint_to_labels_caps_industries_and_tags() -> None:
    from invite_finder.classify_rules import ClassificationHint

    hint = ClassificationHint(
        industries=[Industry.VC_INVESTOR, Industry.BIOTECH_PHARMA, Industry.FINTECH, Industry.SECURITY],
        tags=["a", "b", "c", "d", "e", "f", "g"],
    )
    labels = hint_to_labels(hint)
    assert len(labels["industries"]) <= 3
    assert len(labels["tags"]) <= 6
