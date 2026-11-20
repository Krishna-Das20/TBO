package main

import (
	"net/url"
	"os"
	"path/filepath"
	"testing"
)

func TestScope(t *testing.T) {
	base, _ := url.Parse("http://x")
	tc := &TargetConfig{Name: "v", base: base, AllowPaths: []string{"/api/", "/portal"}, DenyPaths: []string{"/api/admin"},
		AllowMethod: []string{"GET", "POST"}}
	cases := []struct {
		m, p string
		ok   bool
	}{
		{"GET", "/api/v3/bookings", true},
		{"GET", "/portal", true},
		{"GET", "/__ops/release", false},
		{"GET", "/api/admin/users", false},
		{"DELETE", "/api/v3/bookings/1", false},
	}
	for _, c := range cases {
		if ok, why := tc.inScope(c.m, c.p); ok != c.ok {
			t.Errorf("%s %s: got %v (%s)", c.m, c.p, ok, why)
		}
	}
}

func TestAuditChainDetectsTampering(t *testing.T) {
	p := filepath.Join(t.TempDir(), "audit.jsonl")
	a, err := newAuditor(p)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { a.Close() })
	for i := 0; i < 5; i++ {
		a.Write(Record{Run: "r1", Method: "GET", Path: "/api/x", Decision: "allowed"})
	}
	if n, err := a.Verify(); err != nil || n != 5 {
		t.Fatalf("verify clean chain: n=%d err=%v", n, err)
	}
	raw, _ := os.ReadFile(p)
	tampered := []byte(string(raw))
	for i := range tampered { // flip the first "/api/x" to "/api/y"
		if string(tampered[i:i+6]) == "/api/x" {
			tampered[i+5] = 'y'
			break
		}
	}
	os.WriteFile(p, tampered, 0o600)
	if _, err := a.Verify(); err == nil {
		t.Fatal("tampered chain verified")
	}
}

func TestGuardBacksOffAndHalts(t *testing.T) {
	g := newGuard(RateConfig{RPS: 8, MinRPS: 0.5, Burst: 8, Concurrency: 2}, HealthCfg{SlowMs: 500, ErrorRatioHalt: 0.5})
	g.Observe(900, 200, false) // slow
	if r := g.Snapshot()["rate_rps"].(float64); r != 4 {
		t.Fatalf("expected halved rate 4, got %v", r)
	}
	for i := 0; i < 8; i++ {
		g.Observe(50, 503, false)
	}
	if !g.Snapshot()["halted"].(bool) {
		t.Fatal("expected circuit to open after repeated 5xx")
	}
}

func TestRedact(t *testing.T) {
	got := redact(`{"email":"a@b","password":"hunter2","token":"abc"}`)
	if got != `{"email":"a@b","password":"***","token":"***"}` {
		t.Fatal(got)
	}
}
