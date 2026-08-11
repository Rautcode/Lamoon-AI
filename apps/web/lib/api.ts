import { useAuthStore } from "@/lib/auth-store";
import type {
  Application,
  AttendancePolicy,
  DaySummary,
  Department,
  Employee,
  EmployeeAttendance,
  Holiday,
  Job,
  LeaveBalance,
  LeaveRequest,
  LeaveType,
  Me,
  NewDepartment,
  PayrollRun,
  PayrollRunDetail,
  PayrollSettings,
  Payslip,
  Presence,
  WorkWeek,
  NewEmployee,
  NewLeaveRequest,
  NewLeaveType,
} from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function refreshTokens(): Promise<string | null> {
  const refresh = useAuthStore.getState().refreshToken;
  if (!refresh) return null;
  const res = await fetch(`${API_URL}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  useAuthStore.getState().setTokens(data.access_token, data.refresh_token);
  return data.access_token as string;
}

/** Attaches the bearer token; on a 401, refreshes once and retries before
 * giving up and clearing auth (session truly dead — expired refresh token,
 * or the account was deactivated, per core/modules/auth/routes.py::refresh). */
async function request<T>(path: string, init: RequestInit = {}, allowRetry = true): Promise<T> {
  const token = useAuthStore.getState().accessToken;
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_URL}${path}`, { ...init, headers });

  if (res.status === 401 && allowRetry) {
    const newToken = await refreshTokens();
    if (newToken) return request<T>(path, init, false);
    useAuthStore.getState().clear();
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  login: (company: string, email: string, password: string) =>
    request<{ access_token: string; refresh_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ company, email, password }),
    }),
  me: () => request<Me>("/auth/me"),
  oauthProviders: () => request<Record<"google" | "microsoft", boolean>>("/auth/oauth/providers"),
  /** Not a fetch — a real browser navigation to the API, which redirects on
   * to Google/Microsoft. Exported so the login page doesn't hardcode the URL. */
  oauthStartUrl: (provider: "google" | "microsoft", company: string) =>
    `${API_URL}/auth/oauth/${provider}/start?company=${encodeURIComponent(company)}`,
  employees: {
    list: (params?: { department_id?: string }) => {
      const qs = params?.department_id
        ? `?department_id=${encodeURIComponent(params.department_id)}`
        : "";
      return request<Employee[]>(`/hr/employees${qs}`);
    },
    get: (id: string) => request<Employee>(`/hr/employees/${id}`),
    create: (body: NewEmployee) =>
      request<Employee>("/hr/employees", { method: "POST", body: JSON.stringify(body) }),
    /** Provision a login so this person can use self-service. The temp
     *  password is emailed to them and never returned here. */
    invite: (id: string) =>
      request<{ employee_id: string; user_id: string; invited: boolean }>(
        `/hr/employees/${id}/invite`,
        { method: "POST" }
      ),
  },
  /** Employee Self-Service. None of these take an id — the server derives the
   *  person from the JWT, which is the whole security model (see modules/ess). */
  self: {
    profile: () => request<Employee>("/me"),
    balances: () => request<LeaveBalance[]>("/me/leave/balances"),
    requests: () => request<LeaveRequest[]>("/me/leave/requests"),
    /** Record my own check-in/out. Server sets the timestamp. */
    punch: (kind: "in" | "out") =>
      request<DaySummary>("/me/attendance/punch", {
        method: "POST",
        body: JSON.stringify({ kind }),
      }),
    attendance: (days = 14) => request<DaySummary[]>(`/me/attendance?days=${days}`),
    /** "Today" per the COMPANY's timezone — never computed in the browser,
     *  whose UTC date diverges from company-local every evening in IST. */
    attendanceToday: () => request<DaySummary>("/me/attendance/today"),
    fileLeave: (body: {
      leave_type_id: string;
      start_date: string;
      end_date: string;
      reason?: string;
    }) => request<LeaveRequest>("/me/leave/requests", { method: "POST", body: JSON.stringify(body) }),
    /** Finalized runs only — the server filters, so a draft can never reach
     *  an employee as if it were their pay. */
    payslips: () => request<Payslip[]>("/me/payslips"),
  },
  departments: {
    list: () => request<Department[]>("/hr/departments"),
    create: (body: NewDepartment) =>
      request<Department>("/hr/departments", { method: "POST", body: JSON.stringify(body) }),
  },
  leave: {
    types: {
      list: () => request<LeaveType[]>("/leave/types"),
      create: (body: NewLeaveType) =>
        request<LeaveType>("/leave/types", { method: "POST", body: JSON.stringify(body) }),
    },
    requests: {
      list: () => request<LeaveRequest[]>("/leave/requests"),
      create: (body: NewLeaveRequest) =>
        request<LeaveRequest>("/leave/requests", { method: "POST", body: JSON.stringify(body) }),
      approve: (id: string) =>
        request<LeaveRequest>(`/leave/requests/${id}/approve`, { method: "POST" }),
      reject: (id: string) =>
        request<LeaveRequest>(`/leave/requests/${id}/reject`, { method: "POST" }),
    },
    balances: (employeeId: string) => request<LeaveBalance[]>(`/leave/balances/${employeeId}`),
  },
  assistant: {
    /** Lumo. Server decides between the Gemini path and the keyword fallback;
     *  `model_used` reports which one answered. */
    ask: (question: string) =>
      request<{
        text: string;
        items: Array<{ title: string; meta: string | null; href: string | null }>;
        unmatched: boolean;
        model_used: boolean;
      }>("/assistant/ask", { method: "POST", body: JSON.stringify({ question }) }),
  },
  calendar: {
    /** Readable by anyone with a login — everyone needs to know when the
     *  office is shut. Writing needs calendar.write (HR/admin) because it
     *  re-bills every future leave request. */
    holidays: () => request<Holiday[]>("/calendar/holidays"),
    addHoliday: (day: string, name: string) =>
      request<Holiday>("/calendar/holidays", {
        method: "POST",
        body: JSON.stringify({ day, name }),
      }),
    removeHoliday: (id: string) =>
      request<void>(`/calendar/holidays/${id}`, { method: "DELETE" }),
    workWeek: () => request<WorkWeek>("/calendar/work-week"),
    setWorkWeek: (working_days: string) =>
      request<WorkWeek>("/calendar/work-week", {
        method: "PUT",
        body: JSON.stringify({ working_days }),
      }),
  },
  attendance: {
    today: () => request<Presence[]>("/attendance/today"),
    /** Everyone x N days in one call — the heatmap would otherwise be N+1. */
    summary: (days = 14) => request<EmployeeAttendance[]>(`/attendance/summary?days=${days}`),
    policy: () => request<AttendancePolicy>("/attendance/policy"),
  },
  payroll: {
    runs: () => request<PayrollRun[]>("/payroll/runs"),
    run: (id: string) => request<PayrollRunDetail>(`/payroll/runs/${id}`),
    /** Opens the month's draft, or recomputes it if one is already open. */
    compute: (period: string) =>
      request<PayrollRunDetail>("/payroll/runs", {
        method: "POST",
        body: JSON.stringify({ period }),
      }),
    adjust: (runId: string, payslipId: string, patch: { lop_days?: number; tds?: string }) =>
      request<Payslip>(`/payroll/runs/${runId}/payslips/${payslipId}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    finalize: (id: string) =>
      request<PayrollRun>(`/payroll/runs/${id}/finalize`, { method: "POST" }),
    settings: () => request<PayrollSettings>("/payroll/settings"),
  },
  jobs: {
    list: () => request<Job[]>("/ats/jobs"),
  },
  applications: {
    list: () => request<Application[]>("/ats/applications"),
  },
  /** Revokes both tokens server-side, then clears local state regardless of
   * whether the server call succeeds — a network hiccup shouldn't trap the
   * user signed in locally with no way out. */
  logout: async () => {
    const refresh = useAuthStore.getState().refreshToken;
    if (refresh) {
      try {
        await request<void>("/auth/logout", {
          method: "POST",
          body: JSON.stringify({ refresh_token: refresh }),
        });
      } catch {
        // best-effort — see doc comment above
      }
    }
    useAuthStore.getState().clear();
  },
};
