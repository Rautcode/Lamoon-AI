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

export default function EmployeesPage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((s) => s.permissions);
  const canWrite = hasPermission(permissions, "employee.write");

  const { data: employees, isLoading, isError } = useQuery({
    queryKey: ["employees"],
    queryFn: api.employees.list,
  });

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: () => api.employees.create({ full_name: fullName, email: email || undefined }),
    onSuccess: () => {
      setFullName("");
      setEmail("");
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
      <h1 className="text-2xl font-semibold">Employee Directory</h1>

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
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {employees.map((emp) => (
                  <TableRow key={emp.id}>
                    <TableCell>{emp.full_name}</TableCell>
                    <TableCell>{emp.email ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant={emp.status === "active" ? "default" : "secondary"}>
                        {emp.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
                {employees.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={3} className="text-center text-muted-foreground">
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
