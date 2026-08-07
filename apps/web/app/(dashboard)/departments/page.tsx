"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// base-ui's Select rejects an empty-string item value, so "no selection" gets
// its own sentinel and is mapped back to undefined before hitting the API.
const NONE = "__none__";

export default function DepartmentsPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((s) => s.permissions);
  const canWrite = hasPermission(permissions, "employee.write");

  const { data: departments, isLoading, isError } = useQuery({
    queryKey: ["departments"],
    queryFn: api.departments.list,
  });
  // Manager is an Employee, not a User — reuse the same list the Employees
  // page already fetches, just for name lookups here.
  const { data: employees } = useQuery({ queryKey: ["employees"], queryFn: () => api.employees.list() });

  const deptName = (id: string | null) => departments?.find((d) => d.id === id)?.name ?? "—";
  const empName = (id: string | null) => employees?.find((e) => e.id === id)?.full_name ?? "—";

  const [name, setName] = useState("");
  const [parentId, setParentId] = useState(NONE);
  const [managerId, setManagerId] = useState(NONE);
  const [formError, setFormError] = useState<string | null>(null);

  const createMutation = useMutation({
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
      setFormError(null);
      queryClient.invalidateQueries({ queryKey: ["departments"] });
    },
    onError: (err: unknown) => {
      setFormError(
        err instanceof ApiError && err.status === 403
          ? "You don't have permission to add departments."
          : "Could not add department."
      );
    },
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Departments</h1>

      {canWrite && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Add department</CardTitle>
          </CardHeader>
          <CardContent>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                createMutation.mutate();
              }}
              className="flex flex-wrap items-end gap-3"
            >
              <div className="space-y-1">
                <Label htmlFor="dept_name">Name</Label>
                <Input id="dept_name" value={name} onChange={(e) => setName(e.target.value)} required />
              </div>
              <div className="space-y-1">
                <Label>Parent department</Label>
                <Select value={parentId} onValueChange={(v) => setParentId(v ?? NONE)}>
                  <SelectTrigger className="w-48">
                    {/* base-ui's SelectValue doesn't auto-mirror the selected
                    item's label (unlike Radix) — it wants this render-prop. */}
                    <SelectValue>
                      {(v: string) => (v === NONE ? "None" : departments?.find((d) => d.id === v)?.name)}
                    </SelectValue>
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
              <div className="space-y-1">
                <Label>Manager</Label>
                <Select value={managerId} onValueChange={(v) => setManagerId(v ?? NONE)}>
                  <SelectTrigger className="w-48">
                    <SelectValue>
                      {(v: string) => (v === NONE ? "None" : employees?.find((e) => e.id === v)?.full_name)}
                    </SelectValue>
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
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending ? "Adding…" : "Add"}
              </Button>
            </form>
            {formError && <p className="mt-2 text-sm text-destructive">{formError}</p>}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="pt-6">
          {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {isError && <p className="text-sm text-destructive">Couldn&apos;t load departments.</p>}
          {departments && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Parent</TableHead>
                  <TableHead>Manager</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {departments.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell>{d.name}</TableCell>
                    <TableCell>{deptName(d.parent_id)}</TableCell>
                    <TableCell>{empName(d.manager_id)}</TableCell>
                  </TableRow>
                ))}
                {departments.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={3} className="text-center text-muted-foreground">
                      No departments yet.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
