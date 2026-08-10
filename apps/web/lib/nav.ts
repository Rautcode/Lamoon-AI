import { CalendarDays, Clock, Home, Network, User, Users, Briefcase } from "lucide-react";
import { hasPermission } from "@/lib/auth-store";

/* One source of truth for "which routes does this account get".

   Both the rail (what to show) and the workspace layout (what to allow) read
   this, so a link can never appear that the guard would bounce, and the guard
   can never block something the rail offers. */

export type NavItem = {
  href: string;
  label: string;
  Icon: typeof Home;
  /** Permission required to use the route at all. */
  need: string;
};

export const NAV: NavItem[] = [
  { href: "/me", label: "Me", Icon: User, need: "self.read" },
  { href: "/home", label: "Home", Icon: Home, need: "employee.read" },
  { href: "/hiring", label: "Hiring", Icon: Briefcase, need: "ats.read" },
  { href: "/people", label: "People", Icon: Users, need: "employee.read" },
  { href: "/time", label: "Time", Icon: CalendarDays, need: "leave.read" },
  { href: "/attendance", label: "Hours", Icon: Clock, need: "attendance.read" },
  { href: "/org", label: "Org", Icon: Network, need: "employee.read" },
];

export function navFor(permissions: string[]): NavItem[] {
  return NAV.filter((i) => hasPermission(permissions, i.need));
}

/** True if this account may open `pathname`. Unknown paths are allowed — the
 *  API is the real authority; this only stops obviously-wrong navigation. */
export function canOpen(permissions: string[], pathname: string): boolean {
  const match = NAV.filter((i) => pathname === i.href || pathname.startsWith(i.href + "/")).sort(
    (a, b) => b.href.length - a.href.length
  )[0];
  return match ? hasPermission(permissions, match.need) : true;
}
