// HAWK safety gateway: the only network path from HAWK's agents to a target.
//
// Every agent request goes to /t/{target}/{path}. The gateway checks scope, waits for the
// adaptive rate limiter, attaches test credentials from the vault, forwards the request, caps
// the response size and appends a hash-chained audit record. Operators can halt and resume
// testing at any moment from /control.
package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"path"
	"strconv"
	"strings"
	"time"
)

var (
	errHalted      = errors.New("target halted")
	errRateTimeout = errors.New("rate limit wait timed out")
	version        = "0.1.0"
)

type Gateway struct {
	targets    map[string]*TargetConfig
	guards     map[string]*Guard
	vault      *Vault
	audit      *Auditor
	agentKey   string
	adminToken string
	client     *http.Client
}

func userAgent(run string) string {
	return "HAWK/" + version + " (+authorized-security-test; run=" + run + ")"
}

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func main() {
	cfg, err := loadConfig(env("GATEWAY_CONFIG", "../../deploy/gateway.targets.json"))
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	vault, err := loadVault(os.Getenv("GATEWAY_VAULT"))
	if err != nil {
		log.Fatalf("vault: %v", err)
	}
	audit, err := newAuditor(env("GATEWAY_AUDIT", "../../data/gateway-audit.jsonl"))
	if err != nil {
		log.Fatalf("audit: %v", err)
	}
	g := &Gateway{targets: map[string]*TargetConfig{}, guards: map[string]*Guard{}, vault: vault, audit: audit,
		agentKey: env("GATEWAY_AGENT_KEY", "dev-only-agent-key"), adminToken: env("GATEWAY_ADMIN_TOKEN", "dev-only-admin"),
		client: &http.Client{Timeout: 20 * time.Second,
			// Never follow redirects: a redirect can point outside scope, and agents need to see it.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	for i := range cfg.Targets {
		t := &cfg.Targets[i]
		g.targets[t.Name] = t
		g.guards[t.Name] = newGuard(t.Rate, t.Health)
		go g.monitor(t)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/t/{target}/{rest...}", g.proxy)
	mux.HandleFunc("GET /control/status", g.admin(g.status))
	mux.HandleFunc("POST /control/halt", g.admin(g.halt))
	mux.HandleFunc("POST /control/resume", g.admin(g.resume))
	mux.HandleFunc("GET /control/audit", g.admin(g.auditQuery))
	mux.HandleFunc("GET /control/audit/verify", g.admin(g.auditVerify))
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { writeJSON(w, 200, map[string]any{"ok": true}) })

	addr := ":" + env("PORT", "8088")
	log.Printf("hawk gateway %s listening on %s with %d target(s)", version, addr, len(g.targets))
	log.Fatal(http.ListenAndServe(addr, mux))
}

// ------------------------------------------------------------------ proxy

func (g *Gateway) proxy(w http.ResponseWriter, r *http.Request) {
	rec := Record{TS: time.Now().UTC().Format(time.RFC3339Nano), Run: r.Header.Get("X-Hawk-Run"),
		Agent: r.Header.Get("X-Hawk-Agent"), Purpose: r.Header.Get("X-Hawk-Purpose"), Target: r.PathValue("target"),
		Method: r.Method, Query: r.URL.RawQuery, Credential: r.Header.Get("X-Hawk-Credential")}

	if subtle.ConstantTimeCompare([]byte(r.Header.Get("X-Hawk-Key")), []byte(g.agentKey)) != 1 {
		g.deny(w, rec, "blocked:auth", "missing or wrong agent key", 401)
		return
	}
	t, ok := g.targets[rec.Target]
	if !ok {
		g.deny(w, rec, "blocked:scope", "unknown target", 403)
		return
	}
	// Clean the path so ../ and doubled slashes cannot step outside the allow list.
	raw := "/" + r.PathValue("rest")
	if strings.Contains(raw, "..") || strings.Contains(strings.ToLower(r.URL.RawPath), "%2e") {
		rec.Path = raw
		g.deny(w, rec, "blocked:scope", "path traversal pattern", 403)
		return
	}
	clean := path.Clean(raw)
	if strings.HasSuffix(raw, "/") && clean != "/" {
		clean += "/"
	}
	rec.Path = clean
	if rec.Run == "" || rec.Purpose == "" {
		g.deny(w, rec, "blocked:scope", "X-Hawk-Run and X-Hawk-Purpose are required", 400)
		return
	}
	if ok, why := t.inScope(r.Method, clean); !ok {
		g.deny(w, rec, "blocked:scope", why, 403)
		return
	}
	if rec.Credential != "" && !g.vault.Has(t.Name, rec.Credential) {
		g.deny(w, rec, "blocked:scope", "unknown credential handle", 403)
		return
	}

	reqBody, _ := io.ReadAll(io.LimitReader(r.Body, 64<<10))
	rec.ReqSHA256, rec.ReqSnippet = sha(reqBody), snippet(reqBody, 512)

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	guard := g.guards[t.Name]
	release, err := guard.Acquire(ctx)
	if err != nil {
		if errors.Is(err, errHalted) {
			g.deny(w, rec, "blocked:halted", guard.Snapshot()["halt_reason"].(string), 503)
		} else {
			g.deny(w, rec, "blocked:rate", err.Error(), 429)
		}
		return
	}
	defer release()

	status, hdr, body, latency, err := g.forward(ctx, t, r, clean, reqBody, rec.Credential, rec.Run, false)
	guard.Observe(latency, status, err != nil)
	rec.LatencyMs = latency
	if err != nil {
		rec.Decision, rec.Reason = "error", err.Error()
		seq := g.audit.Write(rec)
		w.Header().Set("X-Hawk-Seq", strconv.FormatInt(seq, 10))
		writeJSON(w, 502, map[string]any{"blocked": "upstream_error", "reason": err.Error(), "seq": seq})
		return
	}
	truncated := len(body) > t.MaxBody
	if truncated {
		body = body[:t.MaxBody] // minimum evidence: never relay bulk data
	}
	rec.Decision, rec.Status, rec.RespBytes = "allowed", status, len(body)
	rec.RespSHA256, rec.RespSnip = sha(body), snippet(body, 512)
	seq := g.audit.Write(rec)

	for k, vs := range hdr {
		if hopHeader(k) {
			continue
		}
		for _, v := range vs {
			w.Header().Add(k, v)
		}
	}
	w.Header().Set("X-Hawk-Seq", strconv.FormatInt(seq, 10))
	if truncated {
		w.Header().Set("X-Hawk-Truncated", "1")
	}
	w.Header().Del("Content-Length")
	w.WriteHeader(status)
	w.Write(body)
}

func (g *Gateway) forward(ctx context.Context, t *TargetConfig, r *http.Request, clean string, body []byte,
	cred, run string, retried bool) (int, http.Header, []byte, float64, error) {
	u := t.base.String() + clean
	if r.URL.RawQuery != "" {
		u += "?" + r.URL.RawQuery
	}
	out, err := http.NewRequestWithContext(ctx, r.Method, u, bytes.NewReader(body))
	if err != nil {
		return 0, nil, nil, 0, err
	}
	for k, vs := range r.Header {
		lk := strings.ToLower(k)
		if strings.HasPrefix(lk, "x-hawk-") || hopHeader(k) || lk == "host" || lk == "x-serverless-authorization" {
			continue
		}
		for _, v := range vs {
			out.Header.Add(k, v)
		}
	}
	out.Header.Set("User-Agent", userAgent(run))
	idTok := ""
	if t.GCPIDToken {
		if idTok, err = gcpIDToken(t.base.String()); err != nil {
			return 0, nil, nil, 0, err
		}
		out.Header.Set("X-Serverless-Authorization", "Bearer "+idTok)
	}
	if cred != "" {
		tok, err := g.vault.Token(t, cred, idTok, retried)
		if err != nil {
			return 0, nil, nil, 0, err
		}
		out.Header.Set("Authorization", "Bearer "+tok)
	}
	start := time.Now()
	resp, err := g.client.Do(out)
	latency := float64(time.Since(start).Microseconds()) / 1000
	if err != nil {
		return 0, nil, nil, latency, err
	}
	defer resp.Body.Close()
	rb, _ := io.ReadAll(io.LimitReader(resp.Body, int64(t.MaxBody)+1))
	if resp.StatusCode == 401 && cred != "" && !retried { // session expired: log in again once
		return g.forward(ctx, t, r, clean, body, cred, run, true)
	}
	return resp.StatusCode, resp.Header, rb, latency, nil
}

func (g *Gateway) deny(w http.ResponseWriter, rec Record, decision, reason string, code int) {
	rec.Decision, rec.Reason = decision, reason
	seq := g.audit.Write(rec)
	w.Header().Set("X-Hawk-Seq", strconv.FormatInt(seq, 10))
	writeJSON(w, code, map[string]any{"blocked": strings.TrimPrefix(decision, "blocked:"), "reason": reason, "seq": seq})
}

func hopHeader(k string) bool {
	switch strings.ToLower(k) {
	case "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
		"transfer-encoding", "upgrade":
		return true
	}
	return false
}

// ------------------------------------------------------------------ health monitor

func (g *Gateway) monitor(t *TargetConfig) {
	if t.Health.Path == "" {
		return
	}
	guard := g.guards[t.Name]
	fails := 0
	for {
		time.Sleep(time.Duration(t.Health.IntervalS) * time.Second)
		req, _ := http.NewRequest("GET", t.base.String()+t.Health.Path, nil)
		req.Header.Set("User-Agent", userAgent("health"))
		if t.GCPIDToken {
			if tok, err := gcpIDToken(t.base.String()); err == nil {
				req.Header.Set("X-Serverless-Authorization", "Bearer "+tok)
			}
		}
		start := time.Now()
		resp, err := g.client.Do(req)
		ms := float64(time.Since(start).Milliseconds())
		ok := err == nil && resp.StatusCode < 500
		if resp != nil {
			resp.Body.Close()
		}
		if ok {
			fails = 0
		} else {
			fails++
		}
		guard.HealthResult(fails < 3, ms > t.Health.SlowMs)
	}
}

// ------------------------------------------------------------------ control API

func (g *Gateway) admin(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if subtle.ConstantTimeCompare([]byte(r.Header.Get("X-Admin-Token")), []byte(g.adminToken)) != 1 {
			writeJSON(w, 401, map[string]any{"error": "admin token required"})
			return
		}
		h(w, r)
	}
}

func (g *Gateway) status(w http.ResponseWriter, _ *http.Request) {
	out := map[string]any{}
	for name, t := range g.targets {
		s := g.guards[name].Snapshot()
		s["base_url"] = t.base.String()
		s["allow_paths"], s["deny_paths"] = t.AllowPaths, t.DenyPaths
		s["credential_handles"] = g.vault.Handles(name)
		out[name] = s
	}
	writeJSON(w, 200, map[string]any{"version": version, "targets": out})
}

func (g *Gateway) halt(w http.ResponseWriter, r *http.Request) {
	var body struct{ Target, Reason string }
	json.NewDecoder(r.Body).Decode(&body)
	if body.Reason == "" {
		body.Reason = "halted by operator"
	}
	for name, guard := range g.guards {
		if body.Target == "" || body.Target == "*" || body.Target == name {
			guard.Halt(body.Reason)
		}
	}
	g.audit.Write(Record{TS: time.Now().UTC().Format(time.RFC3339Nano), Agent: "operator", Target: body.Target,
		Decision: "control:halt", Reason: body.Reason})
	writeJSON(w, 200, map[string]any{"halted": true})
}

func (g *Gateway) resume(w http.ResponseWriter, r *http.Request) {
	var body struct{ Target string }
	json.NewDecoder(r.Body).Decode(&body)
	for name, guard := range g.guards {
		if body.Target == "" || body.Target == "*" || body.Target == name {
			guard.Resume()
		}
	}
	g.audit.Write(Record{TS: time.Now().UTC().Format(time.RFC3339Nano), Agent: "operator", Target: body.Target,
		Decision: "control:resume"})
	writeJSON(w, 200, map[string]any{"resumed": true})
}

func (g *Gateway) auditQuery(w http.ResponseWriter, r *http.Request) {
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	if limit <= 0 || limit > 5000 {
		limit = 200
	}
	writeJSON(w, 200, map[string]any{"records": g.audit.Query(r.URL.Query().Get("run"), limit)})
}

func (g *Gateway) auditVerify(w http.ResponseWriter, _ *http.Request) {
	n, err := g.audit.Verify()
	if err != nil {
		writeJSON(w, 200, map[string]any{"valid": false, "records": n, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"valid": true, "records": n})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}
