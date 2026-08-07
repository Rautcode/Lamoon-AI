import { redirect } from "next/navigation";

// The workspace layout bounces to /login when there's no token, so this can
// always aim at /home — auth lives in exactly one place.
export default function Index() {
  redirect("/home");
}
