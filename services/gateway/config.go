package main

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strings"
)

// Config is the rules of engagement, loaded once at start. Agents cannot change it.
type Config struct {
	Targets []TargetConfig `json:"targets"`
}

type TargetConfig struct {
	Name        string   `json:"name"`
	BaseURL     string   `json:"base_url"`
	AllowPaths  []string `json:"allow_paths"`
	DenyPaths   []string `json:"deny_paths"`
	AllowMethod []string `json:"allow_methods"`
	// GCPIDToken adds a Google-signed ID token (X-Serverless-Authorization) so a Cloud Run
	// target that only admits this gateway's service account accepts the call.
	GCPIDToken bool       `json:"gcp_id_token"`
	Rate       RateConfig `json:"rate"`
	Health     HealthCfg  `json:"health"`
	Auth       AuthConfig `json:"auth"`
	MaxBody    int        `json:"max_response_bytes"`
	base       *url.URL
}

type RateConfig struct {
	RPS         float64 `json:"rps"`
	MinRPS      float64 `json:"min_rps"`
	Burst       float64 `json:"burst"`
	Concurrency int     `json:"concurrency"`
}

type HealthCfg struct {
	Path           string  `json:"path"`
	SlowMs         float64 `json:"slow_ms"`
	ErrorRatioHalt float64 `json:"error_ratio_halt"`
	IntervalS      int     `json:"interval_s"`
}

type AuthConfig struct {
	LoginPath  string `json:"login_path"`
	TokenField string `json:"token_field"`
}

func loadConfig(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Config
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	for i := range c.Targets {
		t := &c.Targets[i]
		if v := os.Getenv("TARGET_" + strings.ToUpper(t.Name) + "_URL"); v != "" {
			t.BaseURL = v // per-environment override, e.g. the Cloud Run URL
		}
		u, err := url.Parse(strings.TrimRight(t.BaseURL, "/"))
		if err != nil || u.Scheme == "" || u.Host == "" {
			return nil, fmt.Errorf("target %s: bad base_url %q", t.Name, t.BaseURL)
		}
		t.base = u
		if t.Rate.RPS <= 0 {
			t.Rate.RPS = 5
		}
		if t.Rate.MinRPS <= 0 {
			t.Rate.MinRPS = 0.5
		}
		if t.Rate.Burst <= 0 {
			t.Rate.Burst = t.Rate.RPS * 2
		}
		if t.Rate.Concurrency <= 0 {
			t.Rate.Concurrency = 4
		}
		if t.Health.SlowMs <= 0 {
			t.Health.SlowMs = 800
		}
		if t.Health.ErrorRatioHalt <= 0 {
			t.Health.ErrorRatioHalt = 0.5
		}
		if t.Health.IntervalS <= 0 {
			t.Health.IntervalS = 5
		}
		if t.MaxBody <= 0 {
			t.MaxBody = 256 << 10
		}
		if len(t.AllowMethod) == 0 {
			t.AllowMethod = []string{"GET", "POST", "PUT", "PATCH", "HEAD", "OPTIONS"}
		}
	}
	return &c, nil
}

// inScope applies the allow and deny lists to an already-cleaned path.
func (t *TargetConfig) inScope(method, p string) (bool, string) {
	ok := false
	for _, m := range t.AllowMethod {
		if strings.EqualFold(m, method) {
			ok = true
		}
	}
	if !ok {
		return false, "method " + method + " not allowed"
	}
	for _, d := range t.DenyPaths {
		if strings.HasPrefix(p, d) {
			return false, "path denied by " + d
		}
	}
	for _, a := range t.AllowPaths {
		if a == p || strings.HasPrefix(p, a) {
			return true, ""
		}
	}
	return false, "path not on allow list"
}
