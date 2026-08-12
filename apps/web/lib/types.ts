// Mirrors the FastAPI response_models in apps/api/app/modules/{auth,hr_core,ats}/schemas.py.
// Kept hand-written rather than codegen'd from OpenAPI — revisit if the two
// drift enough to be a recurring bug source.

export type Me = {
  user_id: string;
  company_id: string;
  role: string;
  permissions: string[];
  email: string | null;
  full_name: string | null;
};

export type Employee = {
  id: string;
  /** Present once the person has a login (self-service access). */
  user_id: string | null;
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
  candidate_name: string | null;
  candidate_email: string | null;
  final_score: number | null;
  summary: string | null;
};

export type DaySummary = {
  day: string;
  first_in: string | null;
  last_out: string | null;
  worked_minutes: number;
  open: boolean;
  late: boolean;
  short: boolean;
  anomalies: string[];
};

export type Presence = {
  employee_id: string;
  full_name: string;
  status: "in" | "out" | "absent";
  first_in: string | null;
  last_out: string | null;
  worked_minutes: number;
  late: boolean;
};

export type EmployeeAttendance = {
  employee_id: string;
  full_name: string;
  days: DaySummary[];
};

export type AttendancePolicy = {
  workday_start: string;
  expected_minutes: number;
  grace_minutes: number;
  timezone: string;
};

export type Holiday = {
  id: string;
  day: string;
  name: string;
};

export type WorkWeek = {
  /** Monday-first, seven chars, "1" = worked. */
  working_days: string;
};

/** One line on a payslip. Amounts are strings: they come from Postgres
 *  NUMERIC and must not round-trip through a JS number, which cannot hold
 *  every rupee-and-paise value exactly. Format them, don't compute with them. */
export type PayLine = {
  code: string;
  name: string;
  amount: string;
  /** Where the line came from, once the input ledger generated it. */
  source?: string;
  /** e.g. "6.00 x 250.00" for an overtime line. */
  basis?: string;
};

export type PayslipBreakdown = {
  earnings: PayLine[];
  deductions: PayLine[];
  employer_contributions: PayLine[];
  basis: {
    pf_wage: string;
    esi_wage: string;
    proration: string;
    /** How the statutory wage was derived. Present from the Code on Wages
     *  engine onward; absent on payslips frozen before it. */
    statutory_wage?: string;
    nominated_wages?: string;
    excluded_allowances?: string;
    remuneration?: string;
    added_back?: string;
    /** Why the employer's 12% split the way it did. */
    eps?: string;
  };
  /** The rules this payslip was computed under, resolved by PERIOD. A frozen
   *  payslip records these so it stays defensible years later. */
  rule_versions?: { wage_definition: string; epf: string; esi: string };
};

export type Payslip = {
  id: string;
  run_id: string;
  employee_id: string;
  employee_name: string;
  /** First day of the pay month, snapshotted onto the payslip so it reads on
   *  its own without joining back to the run. */
  period: string;
  working_days: number;
  paid_days: number;
  lop_days: number;
  lop_overridden: boolean;
  gross: string;
  deductions: string;
  net: string;
  employer_cost: string;
  tds: string;
  tds_source: string | null;
  tds_tax_year: string | null;
  tds_note: string | null;
  tds_provided_at: string | null;
  breakdown: PayslipBreakdown;
};

export type PayrollRun = {
  id: string;
  period: string;
  status: "draft" | "finalized";
  finalized_at: string | null;
  gross_total: string;
  deductions_total: string;
  net_total: string;
  employer_cost_total: string;
  /** Top-up to the EPF administration minimum. Levied per establishment per
   *  month, so it belongs to the run rather than to any one payslip. */
  admin_shortfall: string;
};

export type PayrollRunDetail = PayrollRun & { payslips: Payslip[] };

export type PayrollSettings = {
  pf_enabled: boolean;
  esi_enabled: boolean;
  pf_wage_ceiling: string;
  esi_wage_ceiling: string;
  pf_on_full_wage: boolean;
};

/** A line that can appear on a payslip. The three booleans are what the
 *  statutory engine reads — "is this part of PF wages?" has no answer the
 *  software can infer from a name. */
export type PayComponent = {
  id: string;
  code: string;
  name: string;
  kind: "earning" | "deduction";
  /** How this component counts toward the STATUTORY WAGE (Code on Wages,
   *  from 21 Nov 2025). "wages" = basic/DA, always in; "excluded" = out of
   *  wages but counted in the remuneration the 50% test measures against;
   *  "outside" = not remuneration at all (reimbursement of actual expense). */
  wage_basis: "wages" | "excluded" | "outside";
  /** Superseded by wage_basis; the server keeps it in step. */
  pf_wage: boolean;
  esi_wage: boolean;
  taxable: boolean;
  sequence: number;
};

export type NewPayComponent = Omit<PayComponent, "id">;

/** Professional tax is per-STATE, so the schedule is data the customer enters
 *  from their own state, not a rule shipped in code. `up_to: null` is the
 *  unbounded top slab. */
export type PTSlab = { id: string; up_to: string | null; amount: string };

export type SalaryLine = {
  component_id: string;
  code: string;
  name: string;
  kind: string;
  amount: string;
};

export type SalaryStructure = {
  employee_id: string;
  components: SalaryLine[];
  monthly_gross: string;
};

/** One thing wrong, or one thing worth a second look. `impact` is money at
 *  stake where it can be estimated — null means "not quantifiable", which is
 *  different from zero. */
export type Finding = {
  code: string;
  severity: "blocking" | "warning" | "info";
  message: string;
  employee_id: string | null;
  employee_name: string | null;
  impact: string | null;
  detail: Record<string, string>;
};

/** Validation and risk share a shape but are never merged: validation asks
 *  whether the inputs are valid, risk asks whether anything looks unusual. */
export type FindingReport = {
  period: string;
  blocking: number;
  warnings: number;
  info: number;
  impact: string;
  groups: { code: string; severity: string; count: number; impact: string }[];
  findings: Finding[];
};

/** One approved input for one employee for one period. Carries its own
 *  provenance — payroll asks what was approved, not what someone is paid. */
export type PayrollInput = {
  id: string;
  employee_id: string;
  period: string;
  kind: "earning" | "deduction" | "overtime" | "lop" | "adjustment" | "tax";
  code: string;
  name: string;
  amount: string;
  quantity: string | null;
  rate: string | null;
  wage_basis: "wages" | "excluded" | "outside";
  source: "structure" | "work_facts" | "manual" | "import" | "adjustment";
  reason: string | null;
  approved_at: string | null;
  locked: boolean;
  sequence: number;
};

export type RebuildResult = {
  period: string;
  employees: number;
  derived: number;
  preserved: number;
  pending: number;
};

/** One configuration or coverage check. `unknown` means the system cannot
 *  determine it — never rendered as a pass. */
export type ReadinessCheck = {
  code: string;
  label: string;
  status: "ok" | "warning" | "blocking" | "unknown";
  detail: string;
  count: number | null;
};

export type Readiness = {
  period: string;
  /** A summary, never the answer on its own — always read with `blocking`. */
  percent: number;
  blocking: number;
  warnings: number;
  unknown: number;
  checks: ReadinessCheck[];
};

export type MovementTotals = {
  employees: number;
  gross: string;
  deductions: string;
  net: string;
  employer_cost: string;
  pf: string;
  esi: string;
  pt: string;
  tds: string;
};

export type MovementLine = {
  code: string;
  label: string;
  previous: string;
  current: string;
  change: string;
};

/** One cause of the change in gross. `count` is headcount where the cause is
 *  people rather than rates. */
export type BridgeLine = {
  code: string;
  label: string;
  amount: string;
  count: number | null;
};

export type Movement = {
  period: string;
  previous_period: string;
  /** False when there is no prior period — a first payroll has nothing to
   *  move from. */
  comparable: boolean;
  current: MovementTotals;
  previous: MovementTotals;
  lines: MovementLine[];
  bridge: BridgeLine[];
  /** Should always be zero. Shown if it ever isn't. */
  unexplained: string;
};
