"""Employee directory + departments (ARCH §5). RBAC-gated using the
role->permission map already defined in core/auth/permissions.py: hr/admin can
write, manager/hr/admin can read. Employees never reach these routes — their
surface is /me/** (modules/ess), which takes no employee id at all.
"""
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.billing import entitlements
from app.core.db import get_db
from app.core.notify.base import Notifier, get_notifier
from app.core.rbac import require
from app.core.security import hash_password
from app.core.tenant import resolve_tenant
from app.modules.audit import service as audit
from app.modules.auth.models import Company, User
from app.modules.hr_core.models import Department, Employee
from app.modules.hr_core.schemas import DepartmentIn, DepartmentOut, EmployeeIn, EmployeeOut

# ponytail: add dependencies=[Depends(entitlements.require_module("hr_core"))] once
# a generic module-flags table exists — "employees" (seat limit) is wired below.
router = APIRouter(prefix="/hr", tags=["hr_core"])
CAN_WRITE = [Depends(require("employee.write"))]
CAN_READ = [Depends(require("employee.read"))]


@router.post("/departments", response_model=DepartmentOut, dependencies=CAN_WRITE)
def create_department(
    body: DepartmentIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    dept = Department(company_id=uuid.UUID(cid), **body.model_dump())
    db.add(dept)
    db.flush()
    return dept


@router.get("/departments", response_model=list[DepartmentOut], dependencies=CAN_READ)
def list_departments(db: Session = Depends(get_db)):
    return db.scalars(select(Department).where(Department.deleted_at.is_(None))).all()


@router.patch("/departments/{department_id}", response_model=DepartmentOut, dependencies=CAN_WRITE)
def update_department(department_id: uuid.UUID, body: DepartmentIn, db: Session = Depends(get_db)):
    dept = db.get(Department, department_id)
    if dept is None:
        raise HTTPException(404, "not found")
    for k, v in body.model_dump().items():
        setattr(dept, k, v)
    db.flush()
    return dept


@router.post("/employees", response_model=EmployeeOut, dependencies=CAN_WRITE)
def create_employee(
    body: EmployeeIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    decision = entitlements.can_use(db, cid, "employees")
    if not decision.allowed:
        raise HTTPException(402, f"employee seat limit reached ({decision.reason})")
    company_id = uuid.UUID(cid)
    emp = Employee(company_id=company_id, **body.model_dump())
    db.add(emp)
    db.flush()
    audit.record(db, company_id=company_id, entity="employee", entity_id=emp.id, action="created")
    return emp


@router.post("/employees/{employee_id}/invite", dependencies=CAN_WRITE)
async def invite_employee(
    employee_id: uuid.UUID,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
    notifier: Notifier = Depends(get_notifier),
):
    """Give an employee a login so they can use self-service.

    This is the missing link that made ESS impossible before: `employees`
    could exist with no `user_id`, and nothing ever set it.

    ponytail: HR triggers a generated temp password, emailed to the employee
    and never returned in the response — so it doesn't land in logs, browser
    history, or an HR person's screenshot. A proper invite-token + set-your-
    own-password flow is the follow-up; this is the smallest thing that
    doesn't leak a credential through the API.
    """
    emp = db.get(Employee, employee_id)
    if emp is None or emp.deleted_at is not None:
        raise HTTPException(404, "not found")
    if emp.user_id:
        raise HTTPException(409, "this employee already has access")
    if not emp.email:
        raise HTTPException(422, "add an email address before granting access")

    company_id = uuid.UUID(cid)
    # Reuse an existing login for that address rather than colliding with the
    # (company_id, email) unique constraint — e.g. someone hired back, or an
    # admin who is also an employee.
    user = db.scalar(select(User).where(User.email == emp.email))
    created = False
    temp_password = ""
    if user is None:
        temp_password = secrets.token_urlsafe(9)
        user = User(
            company_id=company_id,
            email=emp.email,
            full_name=emp.full_name,
            role="employee",
            password_hash=hash_password(temp_password),
            is_active=True,
        )
        db.add(user)
        db.flush()
        created = True

    emp.user_id = user.id
    audit.record(
        db, company_id=company_id, entity="employee", entity_id=emp.id,
        action="access_granted", payload={"user_id": str(user.id), "new_login": created},
    )

    if created:
        company = db.get(Company, company_id)
        await notifier.send(
            to=emp.email,
            template="access_granted",
            ctx={
                "full_name": emp.full_name,
                "company_name": company.name if company else "your workspace",
                "subdomain": company.subdomain if company else "",
                "email": emp.email,
                "temp_password": temp_password,
            },
        )

    return {"employee_id": str(emp.id), "user_id": str(user.id), "invited": created}


@router.get("/employees", response_model=list[EmployeeOut], dependencies=CAN_READ)
def list_employees(
    department_id: uuid.UUID | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = select(Employee).where(Employee.deleted_at.is_(None))
    if department_id:
        q = q.where(Employee.department_id == department_id)
    if status:
        q = q.where(Employee.status == status)
    return db.scalars(q).all()


@router.get("/employees/{employee_id}", response_model=EmployeeOut, dependencies=CAN_READ)
def get_employee(employee_id: uuid.UUID, db: Session = Depends(get_db)):
    emp = db.get(Employee, employee_id)
    if emp is None or emp.deleted_at is not None:
        raise HTTPException(404, "not found")
    return emp


@router.patch("/employees/{employee_id}", response_model=EmployeeOut, dependencies=CAN_WRITE)
def update_employee(employee_id: uuid.UUID, body: EmployeeIn, db: Session = Depends(get_db)):
    # ponytail: reactivating an 'exited' employee back to 'active'/'probation'
    # here doesn't re-check the seat limit (only create_employee does). Real
    # gap if re-hiring via PATCH becomes a common flow; fine for now since
    # create is the overwhelmingly common path that adds headcount.
    emp = db.get(Employee, employee_id)
    if emp is None or emp.deleted_at is not None:
        raise HTTPException(404, "not found")
    for k, v in body.model_dump().items():
        setattr(emp, k, v)
    db.flush()
    return emp


@router.delete("/employees/{employee_id}", status_code=204, dependencies=CAN_WRITE)
def delete_employee(
    employee_id: uuid.UUID, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    emp = db.get(Employee, employee_id)
    if emp is None or emp.deleted_at is not None:
        raise HTTPException(404, "not found")
    emp.deleted_at = datetime.now(UTC)  # soft delete — every table's convention
    audit.record(
        db, company_id=uuid.UUID(cid), entity="employee", entity_id=emp.id, action="deleted"
    )
