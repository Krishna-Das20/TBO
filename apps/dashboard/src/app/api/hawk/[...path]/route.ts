// Server-side proxy to the HAWK API. The API token never reaches the browser, and only the paths
// below can be called through it.
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const API = process.env.HAWK_API_URL ?? "http://127.0.0.1:8090";
const TOKEN = process.env.HAWK_API_TOKEN ?? "dev-only-api-token";

const GET_ALLOW = [
  /^runs$/,
  /^runs\/[\w.-]+\/events$/,
  /^approvals$/,
  /^findings$/,
  /^findings\/HAWK-[A-F0-9]+\.md$/,
  /^inventory\/[\w-]+$/,
  /^openapi\/[\w-]+\.json$/,
  /^llm$/,
  /^gateway$/,
  /^gateway\/audit$/,
  /^events\/recent$/,
  /^stream$/,
];
const POST_ALLOW = [/^scans$/, /^runs\/[\w.-]+\/approvals$/, /^gateway\/(halt|resume)$/];

async function forward(req: NextRequest, path: string[], allow: RegExp[]) {
  const p = path.join("/");
  if (!allow.some((rx) => rx.test(p))) {
    return Response.json({ error: "not allowed" }, { status: 404 });
  }
  const url = `${API}/${p}${req.nextUrl.search}`;
  const init: RequestInit = {
    method: req.method,
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
    cache: "no-store",
    signal: req.signal,
  };
  if (req.method === "POST") init.body = await req.text();
  let upstream: Response;
  try {
    upstream = await fetch(url, init);
  } catch {
    return Response.json({ error: "HAWK API unreachable" }, { status: 502 });
  }
  const type = upstream.headers.get("content-type") ?? "application/json";
  if (p === "stream") {
    return new Response(upstream.body, {
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", Connection: "keep-alive" },
    });
  }
  return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": type } });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path, GET_ALLOW);
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path, POST_ALLOW);
}
