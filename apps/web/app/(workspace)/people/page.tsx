"use client";
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { Action, Avatar, Empty, Status } from "@/components/lamoon/primitives";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const ALL = "__all__";
const NONE = "__none__";

const TONE = { active: "positive", probation: "caution", exited: "neutral" } as const;

export default function PeoplePage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((s) => s.permissions);
  const canWrite = hasPermission(permissions, "employee.write");

  const [filterDept, setFilterDept] = useState(ALL);
  const [composing, setComposing] = useState(false);

  const { data: departments } = useQuery({ queryKey: ["departments"], queryFn: api.departments.list });
  const { data: employees, isLoading } = useQuery({
    queryKey: ["employees", filterDept],
    queryFn: () => api.employees.list(filterDept === ALL ? undefined : { department_id: filterDept }),
  });

  const deptName = (id: string | null) => departments?.find((d) => d.id === id)?.name;

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [departmentId, setDepartmentId] = useState(NONE);
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.employees.create({
        full_name: fullName,
        email: email || undefined,
        department_id: departmentId === NONE ? undefined : departmentId,
      }),
    onSuccess: () => {
      setFullName("");
      setEmail("");
      setDepartmentId(NONE);
      setFormError(null);
      setComposing(false);
      queryClient.invalidateQueries({ queryKey: ["employees"] });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 402)
        setFormError("Seat limit reached — upgrade your plan to add more people.");
      else if (err instanceof ApiError && err.status === 403)
        setFormError("You don't have permission to add people.");
      else setFormError("Could not add this person.");
    },
  });

  return (
    <div className="fade">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="t-display">People</h1>
          <p className="mt-2 t-meta">
            {employees?.length ?? 0} {employees?.length === 1 ? "person" : "people"}
            {filterDept !== ALL && deptName(filterDept) ? ` in ${deptName(filterDept)}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={filterDept} onValueChange={(v) => setFilterDept(v ?? ALL)}>
            <SelectTrigger className="w-44">
              <SelectValue>
                {(v: string) => (v === ALL ? "All departments" : deptName(v) ?? "—")}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All departments</SelectItem>
              {departments?.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {canWrite && (
            <Action onClick={() => setComposing((v) => !v)}>
              <Plus size={16} />
              Add
            </Action>
          )}
        </div>
      </header>

      {composing && canWrite && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
          className="surface-raised pop mb-8 flex flex-wrap items-end gap-3 p-5"
        >
          <div className="space-y-1.5">
            <Label htmlFor="full_name" className="t-micro">
              Full name
            </Label>
            <Input id="full_name" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="email" className="t-micro">
              Email
            </Label>
            <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label className="t-micro">Department</Label>
            <Select value={departmentId} onValueChange={(v) => setDepartmentId(v ?? NONE)}>
              <SelectTrigger className="w-44">
                <SelectValue>{(v: string) => (v === NONE ? "None" : deptName(v) ?? "—")}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>None</SelectItem>
                {departments?.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Action type="submit" disabled={create.isPending}>
            {create.isPending ? "Adding…" : "Add person"}
          </Action>
          {formError && (
            <p className="w-full text-[0.8125rem] text-[var(--critical)]">{formError}</p>
          )}
        </form>
      )}

      {isLoading && <Empty>Loading…</Empty>}
      {employees?.length === 0 && <Empty>Nobody here yet.</Empty>}

      {/* Tiles, not table rows — a directory is for finding a human. */}
      <div className="stagger grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
        {employees?.map((e, i) => (
          <Link
            key={e.id}
            href={`/people/${e.id}`}
            style={{ "--i": i } as React.CSSProperties}
            className="surface flex items-center gap-3.5 p-4 transition-all duration-200
                       ease-[var(--ease-out-expo)] hover:-translate-y-0.5
                       hover:shadow-[0_8px_24px_-14px_oklch(0_0_0/18%)]"
          >
            <Avatar name={e.full_name} size={42} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[0.9375rem] font-medium">{e.full_name}</p>
              <p className="truncate text-[0.8125rem] text-[var(--ink-3)]">
                {deptName(e.department_id) ?? e.email ?? "—"}
              </p>
            </div>
            <Status tone={TONE[e.status] ?? "neutral"}>{""}</Status>
          </Link>
        ))}
      </div>
    </div>
  );
}
