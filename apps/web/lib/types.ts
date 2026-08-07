// Mirrors the FastAPI response_models in apps/api/app/modules/{auth,hr_core,ats}/schemas.py.
// Kept hand-written rather than codegen'd from OpenAPI — revisit if the two
// drift enough to be a recurring bug source.

export type Me = {
  user_id: string;
  company_id: string;
  role: string;
  permissions: string[];
};

export type Employee = {
  id: string;
  full_name: string;
  email: string | null;
  department_id: string | null;
  reporting_manager_id: string | null;
  status: "active" | "probation" | "exited";
  joined_on: string | null;
};

export type NewEmployee = {
  full_name: string;
  email?: string;
  department_id?: string;
  reporting_manager_id?: string;
  status?: string;
  joined_on?: string;
};

export type Department = {
  id: string;
  name: string;
  parent_id: string | null;
  manager_id: string | null; // an Employee id, not a User id
};

export type NewDepartment = {
  name: string;
  parent_id?: string;
  manager_id?: string;
};

export type LeaveType = {
  id: string;
  name: string;
  annual_quota: number;
};

export type NewLeaveType = {
  name: string;
  annual_quota: number;
};

export type LeaveRequest = {
  id: string;
  employee_id: string;
  leave_type_id: string;
  start_date: string;
  end_date: string;
  days: number;
  reason: string | null;
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  decided_at: string | null;
};

export type NewLeaveRequest = {
  employee_id: string;
  leave_type_id: string;
  start_date: string;
  end_date: string;
  reason?: string;
};

export type LeaveBalance = {
  leave_type_id: string;
  leave_type_name: string;
  allocated: number;
  used: number;
  remaining: number;
};

export type Job = {
  id: string;
  title: string;
  status: string;
};

export type Application = {
  id: string;
  status: string;
  tier: "A" | "B" | "C" | "D" | null;
  recommended_action: string | null;
  candidate_id: string;
  job_opening_id: string | null;
};
