"""Role → permission map. ponytail: 4 fixed V1 roles = a dict, not roles/
role_permissions tables (ADR-0008 designed those for when tenants define custom
roles). "*" = superuser. Extract to DB when custom roles are actually sold.
"""
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"*"}),
    "hr": frozenset(
        {
            "ats.read", "ats.write", "employee.read", "employee.write",
            "leave.read", "leave.write", "leave.approve",
            "attendance.read", "attendance.write",
            "calendar.write",
            "payroll.read", "payroll.write",
        }
    ),
    # manager deliberately has NO payroll.read. Whether a manager may see their
    # team's pay is a policy call every company answers differently, and the
    # safe default for the most confidential data in the product is no.
    # manager approves their team's leave but doesn't administer HR data —
    # leave.write (filing a request on someone's behalf) stays HR-only until
    # there's a real ESS flow where employees file their own.
    "manager": frozenset(
        {"ats.read", "employee.read", "leave.read", "leave.approve", "attendance.read"}
    ),
    # ESS. `self.*` grants access to /me/** only — those routes derive the
    # employee from the JWT and never accept an id, so an employee physically
    # cannot address anyone else's record. They deliberately have no
    # employee.read: seeing the whole directory is not self-service.
    "employee": frozenset(
        {"self.read", "self.leave.write", "self.attendance.write", "self.payslip.read"}
    ),
}


def permissions_for(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())
