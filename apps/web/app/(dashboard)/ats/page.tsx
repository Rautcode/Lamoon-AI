"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

const TIER_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  A: "default",
  B: "secondary",
  C: "outline",
  D: "destructive",
};

export default function AtsPage() {
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: api.jobs.list });
  const appsQuery = useQuery({ queryKey: ["applications"], queryFn: api.applications.list });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">ATS Pipeline</h1>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Open Jobs</CardTitle>
        </CardHeader>
        <CardContent>
          {jobsQuery.data && jobsQuery.data.length > 0 ? (
            <ul className="space-y-2 text-sm">
              {jobsQuery.data.map((j) => (
                <li key={j.id} className="flex items-center justify-between">
                  <span>{j.title}</span>
                  <Badge variant="outline">{j.status}</Badge>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No jobs posted yet.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Applications</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Status</TableHead>
                <TableHead>Tier</TableHead>
                <TableHead>Recommended action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {appsQuery.data?.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>{a.status}</TableCell>
                  <TableCell>
                    {a.tier ? <Badge variant={TIER_VARIANT[a.tier]}>{a.tier}</Badge> : "—"}
                  </TableCell>
                  <TableCell>{a.recommended_action ?? "—"}</TableCell>
                </TableRow>
              ))}
              {appsQuery.data && appsQuery.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={3} className="text-center text-muted-foreground">
                    No applications yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
