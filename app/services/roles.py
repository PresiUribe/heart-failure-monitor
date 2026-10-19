ROLE_CANONICAL = {
    "ADMIN": "admin",
    "MEDICO": "medico",
    "MEDICO_RURAL": "medico",
    "ESPECIALISTA": "especialista",
    "PACIENTE": "paciente",
    "PACIENTE_CUIDADOR": "paciente",
    "CONTABLE": "contable",
}


def canonical_role(code: str) -> str:
    return ROLE_CANONICAL.get((code or "").upper(), (code or "").lower())


def role_matches(actual_code: str, *allowed: str) -> bool:
    actual = canonical_role(actual_code)
    normalized = {canonical_role(x) for x in allowed}
    return actual in normalized


def is_clinical(code: str) -> bool:
    return canonical_role(code) in {"medico", "especialista"}
