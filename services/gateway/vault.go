package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"sync"
	"time"
)

// Vault holds the test accounts. Agents only ever name a handle (for example "skyline.sub");
// the gateway logs in and attaches the session token, so passwords and tokens never reach
// an agent, a prompt or a report.
type Vault struct {
	mu     sync.Mutex
	creds  map[string]map[string]map[string]any // target -> handle -> login body
	tokens map[string]cachedToken
	client *http.Client
}

type cachedToken struct {
	value  string
	expiry time.Time
}

func loadVault(path string) (*Vault, error) {
	v := &Vault{creds: map[string]map[string]map[string]any{}, tokens: map[string]cachedToken{},
		client: &http.Client{Timeout: 15 * time.Second}}
	raw := []byte(os.Getenv("GATEWAY_VAULT_JSON")) // Secret Manager mounts it as an env var in the cloud
	if len(raw) == 0 && path != "" {
		var err error
		if raw, err = os.ReadFile(path); err != nil {
			return nil, err
		}
	}
	if len(raw) == 0 {
		return v, nil
	}
	return v, json.Unmarshal(raw, &v.creds)
}

func (v *Vault) Handles(target string) []string {
	out := []string{}
	for h := range v.creds[target] {
		out = append(out, h)
	}
	return out
}

func (v *Vault) Has(target, handle string) bool {
	_, ok := v.creds[target][handle]
	return ok
}

// Token returns a session token for a handle, logging in when the cache is empty or stale.
func (v *Vault) Token(t *TargetConfig, handle string, idTok string, force bool) (string, error) {
	key := t.Name + "/" + handle
	v.mu.Lock()
	if c, ok := v.tokens[key]; ok && !force && time.Now().Before(c.expiry) {
		v.mu.Unlock()
		return c.value, nil
	}
	body, ok := v.creds[t.Name][handle]
	v.mu.Unlock()
	if !ok {
		return "", errors.New("unknown credential handle")
	}
	raw, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", t.base.String()+t.Auth.LoginPath, bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", userAgent("vault-login"))
	if idTok != "" {
		req.Header.Set("X-Serverless-Authorization", "Bearer "+idTok)
	}
	resp, err := v.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	rb, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("login for %s failed: HTTP %d", handle, resp.StatusCode)
	}
	var parsed map[string]any
	if err := json.Unmarshal(rb, &parsed); err != nil {
		return "", err
	}
	tok, _ := parsed[t.Auth.TokenField].(string)
	if tok == "" {
		return "", errors.New("login response had no token field")
	}
	v.mu.Lock()
	v.tokens[key] = cachedToken{tok, time.Now().Add(45 * time.Minute)}
	v.mu.Unlock()
	return tok, nil
}

// --- Google ID tokens for Cloud Run targets that only admit this gateway's service account.

var (
	idMu    sync.Mutex
	idCache = map[string]cachedToken{}
)

func gcpIDToken(audience string) (string, error) {
	idMu.Lock()
	defer idMu.Unlock()
	if c, ok := idCache[audience]; ok && time.Now().Before(c.expiry) {
		return c.value, nil
	}
	u := "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=" +
		url.QueryEscape(audience)
	req, _ := http.NewRequest("GET", u, nil)
	req.Header.Set("Metadata-Flavor", "Google")
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("metadata server: HTTP %d", resp.StatusCode)
	}
	idCache[audience] = cachedToken{string(b), time.Now().Add(50 * time.Minute)}
	return string(b), nil
}
