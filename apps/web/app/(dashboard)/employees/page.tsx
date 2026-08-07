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
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// base-ui's Select rejects an empty-string item value, so "no selection" gets
// its own sentinel and is mapped back to undefined/omitted at the API boundary.
const ALL = "__all__";
const NONE = "__none__";

export default function EmployeesPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((s) => s.permissions);
  const canWrite = hasPermission(permissions, "employee.write");

  const { data: departments } = useQuery({ queryKey: ["departments"], queryFn: api.departments.list });
  const deptName = (id: string | null) => departments?.find((d) => d.id === id)?.name ?? "—";

  const [filterDept, setFilterDept] = useState(ALL);
  const { data: employees, isLoading, isError } = useQuery({
    queryKey: ["employees", filterDept],
    queryFn: () => api.employees.list(filterDept === ALL ? undefined : { department_id: filterDept }),
  });

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [departmentId, setDepartmentId] = useState(NONE);
  const [formError, setFormError] = useState<string | null>(null);

  const createMutation = useMutation({
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
      queryClient.invalidateQueries({ queryKey: ["employees"] });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 402) {
        setFormError("Employee seat limit reached — upgrade your plan.");
      } else if (err instanceof ApiError && err.status === 403) {
        setFormError("You don't have permission to add employees.");
      } else {
        setFormError("Could not add employee.");
      }
    },
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Employee Directory</h1>
        <div className="flex items-center gap-2">
          <Label className="text-muted-foreground text-sm">Department</Label>
          <Select value={filterDept} onValueChange={(v) => setFilterDept(v ?? ALL)}>
            <SelectTrigger className="w-48">
              {/* base-ui's SelectValue doesn't auto-mirror the selected
              item's label (unlike Radix) — it wants this render-prop. */}
              <SelectValue>
                {(v: string) => (v === ALL ? "All departments" : departments?.find((d) => d.id === v)?.name)}
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
        </div>
      </div>

      {canWrite && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Add employee</CardTitle>
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
                <Label htmlFor="full_name">Full name</Label>
                <Input
                  id="full_name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>Department</Label>
                <Select value={departmentId} onValueChange={(v) => setDepartmentId(v ?? NONE)}>
                  <SelectTrigger className="w-48">
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
          {isError && <p className="text-sm text-destructive">Couldn&apos;t load employees.</p>}
          {employees && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Department</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {employees.map((emp) => (
                  <TableRow key={emp.id}>
                    <TableCell>{emp.full_name}</TableCell>
                    <TableCell>{emp.email ?? "—"}</TableCell>
                    <TableCell>{deptName(emp.department_id)}</TableCell>
                    <TableCell>
                      <Badge variant={emp.status === "active" ? "default" : "secondary"}>
                        {emp.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
                {employees.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={4} className="text-center text-muted-foreground">
                      No employees yet.
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
