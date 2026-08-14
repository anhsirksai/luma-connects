from __future__ import annotations

from enum import Enum

TAXONOMY_VERSION = 1


class FieldCategory(str, Enum):
    SOFTWARE_ENGINEERING = "software_engineering"
    DATA_ML = "data_ml"
    BUSINESS_OPERATIONS = "business_operations"
    RESEARCH_ACADEMIA = "research_academia"
    SALES_GTM = "sales_gtm"
    FINANCE_INVESTING = "finance_investing"
    OTHER = "other"


FIELD_LABELS: dict[FieldCategory, str] = {
    FieldCategory.SOFTWARE_ENGINEERING: "Software engineering",
    FieldCategory.DATA_ML: "Data / ML",
    FieldCategory.BUSINESS_OPERATIONS: "Business / operations",
    FieldCategory.RESEARCH_ACADEMIA: "Research / academia",
    FieldCategory.SALES_GTM: "Sales / GTM",
    FieldCategory.FINANCE_INVESTING: "Finance / investing",
    FieldCategory.OTHER: "Other",
}


class RoleType(str, Enum):
    FOUNDER = "founder"
    WORKING_PROFESSIONAL = "working_professional"
    STUDENT_EARLY_CAREER = "student_early_career"


ROLE_TYPE_LABELS: dict[RoleType, str] = {
    RoleType.FOUNDER: "Founders",
    RoleType.WORKING_PROFESSIONAL: "Working professionals",
    RoleType.STUDENT_EARLY_CAREER: "Students / early-career",
}


class Seniority(str, Enum):
    LEADERSHIP = "leadership"
    SENIOR = "senior"
    MID = "mid"
    JUNIOR = "junior"
    UNKNOWN = "unknown"


SENIORITY_LABELS: dict[Seniority, str] = {
    Seniority.LEADERSHIP: "Leadership",
    Seniority.SENIOR: "Senior",
    Seniority.MID: "Mid",
    Seniority.JUNIOR: "Junior",
    Seniority.UNKNOWN: "Unknown",
}


class Industry(str, Enum):
    VC_INVESTOR = "vc_investor"
    BIOTECH_PHARMA = "biotech_pharma"
    HEALTHCARE = "healthcare"
    AI_INFRA = "ai_infra"
    ROBOTICS_HARDWARE = "robotics_hardware"
    ENTERPRISE_SAAS = "enterprise_saas"
    FINTECH = "fintech"
    CONSUMER = "consumer"
    CRYPTO_WEB3 = "crypto_web3"
    CLIMATE_ENERGY = "climate_energy"
    SECURITY = "security"
    DEVTOOLS = "devtools"
    CONSULTING_SERVICES = "consulting_services"
    MEDIA_MARKETING = "media_marketing"
    GOVERNMENT_NONPROFIT = "government_nonprofit"
    EDUCATION = "education"
    OTHER = "other"


INDUSTRY_LABELS: dict[Industry, str] = {
    Industry.VC_INVESTOR: "VC / investor",
    Industry.BIOTECH_PHARMA: "Biotech / pharma",
    Industry.HEALTHCARE: "Healthcare",
    Industry.AI_INFRA: "AI infrastructure",
    Industry.ROBOTICS_HARDWARE: "Robotics / hardware",
    Industry.ENTERPRISE_SAAS: "Enterprise SaaS",
    Industry.FINTECH: "Fintech",
    Industry.CONSUMER: "Consumer",
    Industry.CRYPTO_WEB3: "Crypto / web3",
    Industry.CLIMATE_ENERGY: "Climate / energy",
    Industry.SECURITY: "Security",
    Industry.DEVTOOLS: "Devtools",
    Industry.CONSULTING_SERVICES: "Consulting / services",
    Industry.MEDIA_MARKETING: "Media / marketing",
    Industry.GOVERNMENT_NONPROFIT: "Government / nonprofit",
    Industry.EDUCATION: "Education",
    Industry.OTHER: "Other",
}


def field_values() -> list[str]:
    return [f.value for f in FieldCategory]


def role_type_values() -> list[str]:
    return [r.value for r in RoleType]


def seniority_values() -> list[str]:
    return [s.value for s in Seniority]


def industry_values() -> list[str]:
    return [i.value for i in Industry]
