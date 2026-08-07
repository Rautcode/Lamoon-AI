"use client";
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { Action, Avatar, Empty, SectionLabel } from "@/components/lamoon/primitives";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/* Org structure. Departments render as a real tree (parent_id), because a flat
   list of departments tells you nothing an org chart is for. */

const NONE = "__none__";

export default function OrgPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((s) => s.permissions);
  const canWrite = hasPermission(permissions, "employee.write");
  const [composing, setComposing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: departments, isLoading } = useQuery({
    queryKey: ["departments"],
    queryFn: api.departments.list,
  });
  const { data: employees } = useQuery({ queryKey: ["employees"], queryFn: () => api.employees.list() });

  const empName = (id: string | null) => employees?.find((e) => e.id === id)?.full_name;
  const headcount = (deptId: string) =>
    employees?.filter((e) => e.department_id === deptId).length ?? 0;

  const [name, setName] = useState("");
  const [parentId, setParentId] = useState(NONE);
  const [managerId, setManagerId] = useState(NONE);

  const create = useMutation({
    mutationFn: () =>
      api.departments.create({
        name,
        parent_id: parentId === NONE ? undefined : parentId,
        manager_id: managerId === NONE ? undefined : managerId,
      }),
    onSuccess: () => {
      setName("");
      setParentId(NONE);
      setManagerId(NONE);
      setComposing(false);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["departments"] });
    },
    onError: (e: unknown) =>
      setError(
        e instanceof ApiError && e.status === 403
          ? "You don't have permission to add departments."
          : "Could not add that department."
      ),
  });

  const deptName = (id: string | null) => departments?.find((d) => d.id === id)?.name;
  const roots = departments?.filter((d) => !d.parent_id) ?? [];
  const childrenOf = (id: string) => departments?.filter((d) => d.parent_id === id) ?? [];

  function Node({ id, name, depth }: { id: string; name: string; depth: number }) {
    const kids = childrenOf(id);
    const manager = empName(departments?.find((d) => d.id === id)?.manager_id ?? null);
    return (
      <div>
        <div
          className="flex items-center gap-3 rounded-[10px] px-3 py-3 hover:bg-[var(--surface-1)]"
          style={{ marginLeft: depth * 20 }}
        >
          {depth > 0 && <span className="text-[var(--ink-4)]">└</span>}
          <span className="min-w-0 flex-1">
            <span className="text-[0.9375rem]">{name}</span>
            {manager && (
              <span className="ml-2 text-[0.8125rem] text-[var(--ink-3)]">· {manager}</span>
            )}
          </span>
          <Link
            href={`/people?department=${id}`}
            className="shrink-0 text-[0.75rem] tabular-nums text-[var(--ink-3)] hover:text-[var(--ink-1)]"
          >
            {headcount(id)} {headcount(id) === 1 ? "person" : "people"}
          </Link>
        </div>
        {kids.map((k) => (
          <Node key={k.id} id={k.id} name={k.name} depth={depth + 1} />
        ))}
      </div>
    );
  }

  return (
    <div className="fade">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="t-display">Org</h1>
          <p className="mt-2 t-meta">
            {departments?.length ?? 0}{" "}
            {departments?.length === 1 ? "department" : "departments"}
          </p>
        </div>
        {canWrite && (
          <Action onClick={() => setComposing((v) => !v)}>
            <Plus size={16} />
            New department
          </Action>
        )}
      </header>

      {error && <p className="mb-4 text-[0.8125rem] text-[var(--critical)]">{error}</p>}

      {composing && canWrite && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
          className="surface-raised pop mb-8 flex flex-wrap items-end gap-3 p-5"
        >
          <div className="space-y-1.5">
            <Label htmlFor="dept_name" className="t-micro">
              Name
            </Label>
            <Input id="dept_name" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label className="t-micro">Parent</Label>
            <Select value={parentId} onValueChange={(v) => setParentId(v ?? NONE)}>
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
          <div className="space-y-1.5">
            <Label className="t-micro">Manager</Label>
            <Select value={managerId} onValueChange={(v) => setManagerId(v ?? NONE)}>
              <SelectTrigger className="w-44">
                <SelectValue>{(v: string) => (v === NONE ? "None" : empName(v) ?? "—")}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>None</SelectItem>
                {employees?.map((e) => (
                  <SelectItem key={e.id} value={e.id}>
                    {e.full_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Action type="submit" disabled={create.isPending}>
            Add
          </Action>
        </form>
      )}

      {isLoading && <Empty>Loading…</Empty>}
      {departments?.length === 0 && <Empty>No departments yet.</Empty>}

      {roots.length > 0 && (
        <section>
          <SectionLabel>Structure</SectionLabel>
          <div className="-mx-3">
            {roots.map((d) => (
              <Node key={d.id} id={d.id} name={d.name} depth={0} />
            ))}
          </div>
        </section>
      )}

      {employees && employees.length > 0 && (
        <section className="mt-12">
          <SectionLabel>Unassigned</SectionLabel>
          {employees.filter((e) => !e.department_id).length === 0 ? (
            <p className="t-meta">Everyone has a department.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {employees
                .filter((e) => !e.department_id)
                .map((e) => (
                  <Link
                    key={e.id}
                    href={`/people/${e.id}`}
                    className="surface flex items-center gap-2.5 px-3 py-2 transition-colors hover:bg-[var(--surface-2)]"
                  >
                    <Avatar name={e.full_name} size={24} />
                    <span className="text-[0.8125rem]">{e.full_name}</span>
                  </Link>
                ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
