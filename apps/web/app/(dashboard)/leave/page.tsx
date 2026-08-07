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

// base-ui's Select rejects an empty-string item value, so "nothing chosen
// yet" gets its own sentinel — validated against before submit, same as the
// Departments/Employees pages.
const UNSET = "__unset__";

const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  approved: "default",
  pending: "secondary",
  rejected: "destructive",
};

export default function LeavePage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((s) => s.permissions);
  const canWrite = hasPermission(permissions, "leave.write");
  const canApprove = hasPermission(permissions, "leave.approve");

  const { data: employees } = useQuery({ queryKey: ["employees"], queryFn: () => api.employees.list() });
  const { data: leaveTypes } = useQuery({ queryKey: ["leave-types"], queryFn: api.leave.types.list });
  const { data: requests, isLoading, isError } = useQuery({
    queryKey: ["leave-requests"],
    queryFn: api.leave.requests.list,
  });

  const empName = (id: string) => employees?.find((e) => e.id === id)?.full_name ?? "—";
  const typeName = (id: string) => leaveTypes?.find((t) => t.id === id)?.name ?? "—";

  // --- leave type configuration ---
  const [newTypeName, setNewTypeName] = useState("");
  const [newTypeQuota, setNewTypeQuota] = useState("");
  const [typeError, setTypeError] = useState<string | null>(null);
  const createType = useMutation({
    mutationFn: () =>
      api.leave.types.create({ name: newTypeName, annual_quota: Number(newTypeQuota) }),
    onSuccess: () => {
      setNewTypeName("");
      setNewTypeQuota("");
      setTypeError(null);
      queryClient.invalidateQueries({ queryKey: ["leave-types"] });
    },
    onError: () => setTypeError("Could not add leave type."),
  });

  // --- balance lookup ---
  const [balanceEmployee, setBalanceEmployee] = useState(UNSET);
  const { data: balances } = useQuery({
    queryKey: ["leave-balances", balanceEmployee],
    queryFn: () => api.leave.balances(balanceEmployee),
    enabled: balanceEmployee !== UNSET,
  });

  // --- file a request ---
  const [reqEmployee, setReqEmployee] = useState(UNSET);
  const [reqType, setReqType] = useState(UNSET);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [reason, setReason] = useState("");
  const [reqError, setReqError] = useState<string | null>(null);
  const createRequest = useMutation({
    mutationFn: () =>
      api.leave.requests.create({
        employee_id: reqEmployee,
        leave_type_id: reqType,
        start_date: startDate,
        end_date: endDate,
        reason: reason || undefined,
      }),
    onSuccess: () => {
      setReqEmployee(UNSET);
      setReqType(UNSET);
      setStartDate("");
      setEndDate("");
      setReason("");
      setReqError(null);
      queryClient.invalidateQueries({ queryKey: ["leave-requests"] });
      queryClient.invalidateQueries({ queryKey: ["leave-balances"] });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 422) {
        setReqError("Check the dates — end can't be before start.");
      } else if (err instanceof ApiError && err.status === 403) {
        setReqError("You don't have permission to file leave requests.");
      } else {
        setReqError("Could not submit the request.");
      }
    },
  });

  function submitRequest(e: React.FormEvent) {
    e.preventDefault();
    if (reqEmployee === UNSET || reqType === UNSET || !startDate || !endDate) {
      setReqError("Choose an employee, a leave type, and both dates.");
      return;
    }
    createRequest.mutate();
  }

  // --- approve / reject ---
  const [decideError, setDecideError] = useState<string | null>(null);
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      action === "approve" ? api.leave.requests.approve(id) : api.leave.requests.reject(id),
    onSuccess: () => {
      setDecideError(null);
      queryClient.invalidateQueries({ queryKey: ["leave-requests"] });
      queryClient.invalidateQueries({ queryKey: ["leave-balances"] });
    },
    onError: (err: unknown) => {
      setDecideError(err instanceof ApiError ? err.message : "Could not update the request.");
    },
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Leave Management</h1>

      {canWrite && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Leave types</CardTitle>
          </CardHeader>
          <CardContent>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                createType.mutate();
              }}
              className="flex flex-wrap items-end gap-3"
            >
              <div className="space-y-1">
                <Label htmlFor="type_name">Name</Label>
                <Input
                  id="type_name"
                  value={newTypeName}
                  onChange={(e) => setNewTypeName(e.target.value)}
                  placeholder="Annual"
                  required
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="type_quota">Annual quota (days)</Label>
                <Input
                  id="type_quota"
                  type="number"
                  min={0}
                  value={newTypeQuota}
                  onChange={(e) => setNewTypeQuota(e.target.value)}
                  required
                />
              </div>
              <Button type="submit" disabled={createType.isPending}>
                {createType.isPending ? "Adding…" : "Add type"}
              </Button>
            </form>
            {typeError && <p className="mt-2 text-sm text-destructive">{typeError}</p>}
            {leaveTypes && leaveTypes.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {leaveTypes.map((t) => (
                  <Badge key={t.id} variant="outline">
                    {t.name}: {t.annual_quota}d/yr
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Check balance</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Select value={balanceEmployee} onValueChange={(v) => setBalanceEmployee(v ?? UNSET)}>
            <SelectTrigger className="w-56">
              <SelectValue>
                {(v: string) => (v === UNSET ? "Choose an employee" : empName(v))}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {employees?.map((e) => (
                <SelectItem key={e.id} value={e.id}>
                  {e.full_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {balanceEmployee !== UNSET && (
            <div className="flex flex-wrap gap-2">
              {balances?.map((b) => (
                <Badge key={b.leave_type_id} variant={b.remaining > 0 ? "secondary" : "destructive"}>
                  {b.leave_type_name}: {b.remaining}/{b.allocated} left
                </Badge>
              ))}
              {balances && balances.length === 0 && (
                <span className="text-sm text-muted-foreground">No leave types configured yet.</span>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {canWrite && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">File a leave request</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={submitRequest} className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label>Employee</Label>
                <Select value={reqEmployee} onValueChange={(v) => setReqEmployee(v ?? UNSET)}>
                  <SelectTrigger className="w-44">
                    <SelectValue>{(v: string) => (v === UNSET ? "Choose" : empName(v))}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {employees?.map((e) => (
                      <SelectItem key={e.id} value={e.id}>
                        {e.full_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Leave type</Label>
                <Select value={reqType} onValueChange={(v) => setReqType(v ?? UNSET)}>
                  <SelectTrigger className="w-40">
                    <SelectValue>{(v: string) => (v === UNSET ? "Choose" : typeName(v))}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {leaveTypes?.map((t) => (
                      <SelectItem key={t.id} value={t.id}>
                        {t.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="start_date">Start</Label>
                <Input
                  id="start_date"
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="end_date">End</Label>
                <Input
                  id="end_date"
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="reason">Reason</Label>
                <Input id="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
              </div>
              <Button type="submit" disabled={createRequest.isPending}>
                {createRequest.isPending ? "Submitting…" : "Submit"}
              </Button>
            </form>
            {reqError && <p className="mt-2 text-sm text-destructive">{reqError}</p>}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="pt-6">
          {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {isError && <p className="text-sm text-destructive">Couldn&apos;t load leave requests.</p>}
          {decideError && <p className="mb-2 text-sm text-destructive">{decideError}</p>}
          {requests && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Employee</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Dates</TableHead>
                  <TableHead>Days</TableHead>
                  <TableHead>Status</TableHead>
                  {canApprove && <TableHead>Actions</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>{empName(r.employee_id)}</TableCell>
                    <TableCell>{typeName(r.leave_type_id)}</TableCell>
                    <TableCell>
                      {r.start_date} → {r.end_date}
                    </TableCell>
                    <TableCell>{r.days}</TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[r.status] ?? "outline"}>{r.status}</Badge>
                    </TableCell>
                    {canApprove && (
                      <TableCell>
                        {r.status === "pending" ? (
                          <div className="flex gap-2">
                            <Button size="sm" onClick={() => decide.mutate({ id: r.id, action: "approve" })}>
                              Approve
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => decide.mutate({ id: r.id, action: "reject" })}
                            >
                              Reject
                            </Button>
                          </div>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                    )}
                  </TableRow>
                ))}
                {requests.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={canApprove ? 6 : 5} className="text-center text-muted-foreground">
                      No leave requests yet.
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
