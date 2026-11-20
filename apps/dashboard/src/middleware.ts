// Optional password gate for the hosted dashboard (set DASHBOARD_PASSWORD). In production put the
// service behind Google Identity-Aware Proxy instead.
import { NextRequest, NextResponse } from "next/server";

export function middleware(req: NextRequest) {
  const password = process.env.DASHBOARD_PASSWORD;
  if (!password) return NextResponse.next();
  const header = req.headers.get("authorization") ?? "";
  const [scheme, encoded] = header.split(" ");
  if (scheme === "Basic" && encoded) {
    const [, pass] = atob(encoded).split(":");
    if (pass === password) return NextResponse.next();
  }
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="HAWK"' },
  });
}

export const config = { matcher: ["/((?!_next/static|_next/image|hawk-logo.png).*)"] };
