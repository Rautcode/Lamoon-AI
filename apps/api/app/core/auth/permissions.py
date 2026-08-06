"""Role → permission map. ponytail: 4 fixed V1 roles = a dict, not roles/
role_permissions tables (ADR-0008 designed those for when tenants define custom
roles). "*" = superuser. Extract to DB when custom roles are actually sold.
"""
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"*"}),
    "hr": frozenset({"ats.read", "ats.write", "employee.read", "employee.write"}),
    "manager": frozenset({"ats.read", "employee.read"}),
    "employee": frozenset({"self.read"}),
}


def permissions_for(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())
