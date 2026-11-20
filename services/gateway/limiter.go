package main

import (
	"context"
	"math"
	"sort"
	"strconv"
	"sync"
	"time"
)

// Guard is one target's load control: an adaptive token bucket (AIMD), a concurrency cap and a
// circuit breaker. It slows down when the target shows strain and stops when it is failing.
type Guard struct {
	mu       sync.Mutex
	cfg      RateConfig
	health   HealthCfg
	rate     float64
	tokens   float64
	last     time.Time
	window   []sample
	okStreak int
	halted   bool
	reason   string
	haltedAt time.Time
	autoHalt bool
	inflight chan struct{}
	backoffs int
	sent     int64
	blocked  int64
}

type sample struct {
	latencyMs float64
	failed    bool
}

func newGuard(cfg RateConfig, h HealthCfg) *Guard {
	return &Guard{cfg: cfg, health: h, rate: cfg.RPS, tokens: cfg.Burst, last: time.Now(),
		inflight: make(chan struct{}, cfg.Concurrency)}
}

// Acquire waits for a token and a concurrency slot. It fails fast when the target is halted.
func (g *Guard) Acquire(ctx context.Context) (release func(), err error) {
	for {
		g.mu.Lock()
		if g.halted {
			g.blocked++
			g.mu.Unlock()
			return nil, errHalted
		}
		now := time.Now()
		g.tokens = math.Min(g.cfg.Burst, g.tokens+now.Sub(g.last).Seconds()*g.rate)
		g.last = now
		if g.tokens >= 1 {
			g.tokens--
			g.mu.Unlock()
			break
		}
		wait := time.Duration((1 - g.tokens) / g.rate * float64(time.Second))
		g.mu.Unlock()
		select {
		case <-ctx.Done():
			return nil, errRateTimeout
		case <-time.After(wait):
		}
	}
	select {
	case g.inflight <- struct{}{}:
	case <-ctx.Done():
		return nil, errRateTimeout
	}
	return func() { <-g.inflight }, nil
}

// Observe feeds one response back. Strain halves the rate; a healthy streak adds back slowly;
// a high failure ratio opens the circuit.
func (g *Guard) Observe(latencyMs float64, status int, transportErr bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.sent++
	failed := transportErr || status >= 500
	g.window = append(g.window, sample{latencyMs, failed})
	if len(g.window) > 20 {
		g.window = g.window[len(g.window)-20:]
	}
	fails := 0
	lats := make([]float64, 0, len(g.window))
	for _, s := range g.window {
		if s.failed {
			fails++
		}
		lats = append(lats, s.latencyMs)
	}
	sort.Float64s(lats)
	p50 := lats[len(lats)/2]
	ratio := float64(fails) / float64(len(g.window))

	if len(g.window) >= 8 && ratio >= g.health.ErrorRatioHalt {
		g.halt("error ratio "+fmtPct(ratio)+" over last "+itoa(len(g.window))+" responses", true)
		return
	}
	if failed || p50 > g.health.SlowMs {
		newRate := math.Max(g.cfg.MinRPS, g.rate/2)
		if newRate < g.rate {
			g.backoffs++
		}
		g.rate = newRate
		g.okStreak = 0
		return
	}
	g.okStreak++
	if g.okStreak >= 10 && g.rate < g.cfg.RPS {
		g.rate = math.Min(g.cfg.RPS, g.rate+0.5)
		g.okStreak = 0
	}
}

func (g *Guard) halt(reason string, auto bool) {
	if !g.halted {
		g.halted, g.reason, g.haltedAt, g.autoHalt = true, reason, time.Now(), auto
	}
}

func (g *Guard) Halt(reason string) { g.mu.Lock(); g.halt(reason, false); g.mu.Unlock() }

func (g *Guard) Resume() {
	g.mu.Lock()
	g.halted, g.reason, g.autoHalt = false, "", false
	g.window = nil
	g.rate = g.cfg.MinRPS // come back gently
	g.mu.Unlock()
}

// HealthResult lets the health monitor close an automatic halt once the target recovers.
func (g *Guard) HealthResult(ok bool, slow bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if !ok {
		g.halt("health check failing", true)
		return
	}
	if g.halted && g.autoHalt && !slow && time.Since(g.haltedAt) > 15*time.Second {
		g.halted, g.reason, g.autoHalt, g.window, g.rate = false, "", false, nil, g.cfg.MinRPS
	}
}

func (g *Guard) Snapshot() map[string]any {
	g.mu.Lock()
	defer g.mu.Unlock()
	return map[string]any{"rate_rps": round2(g.rate), "max_rps": g.cfg.RPS, "halted": g.halted, "halt_reason": g.reason,
		"backoffs": g.backoffs, "requests_sent": g.sent, "requests_blocked": g.blocked}
}

func round2(f float64) float64 { return math.Round(f*100) / 100 }
func fmtPct(f float64) string  { return strconv.Itoa(int(math.Round(f*100))) + "%" }
func itoa(i int) string        { return strconv.Itoa(i) }
