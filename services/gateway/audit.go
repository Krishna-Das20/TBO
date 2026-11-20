package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"sync"
)

// Record is one line of the audit log. Records are hash-chained: each hash covers the record
// and the previous hash, so editing or deleting any line breaks every hash after it.
type Record struct {
	Seq        int64   `json:"seq"`
	TS         string  `json:"ts"`
	Run        string  `json:"run"`
	Agent      string  `json:"agent"`
	Purpose    string  `json:"purpose"`
	Target     string  `json:"target"`
	Method     string  `json:"method"`
	Path       string  `json:"path"`
	Query      string  `json:"query,omitempty"`
	Credential string  `json:"credential,omitempty"`
	Decision   string  `json:"decision"` // allowed | blocked:scope | blocked:halted | blocked:rate | blocked:auth | error
	Reason     string  `json:"reason,omitempty"`
	ReqSHA256  string  `json:"req_sha256,omitempty"`
	ReqSnippet string  `json:"req_snippet,omitempty"`
	Status     int     `json:"status,omitempty"`
	LatencyMs  float64 `json:"latency_ms,omitempty"`
	RespBytes  int     `json:"resp_bytes,omitempty"`
	RespSHA256 string  `json:"resp_sha256,omitempty"`
	RespSnip   string  `json:"resp_snippet,omitempty"`
	Prev       string  `json:"prev"`
	Hash       string  `json:"hash"`
}

type Auditor struct {
	mu     sync.Mutex
	path   string
	f      *os.File
	prev   string
	seq    int64
	recent []Record
}

const genesis = "0000000000000000000000000000000000000000000000000000000000000000"

func newAuditor(path string) (*Auditor, error) {
	a := &Auditor{path: path, prev: genesis}
	if f, err := os.Open(path); err == nil { // continue an existing chain
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1<<20), 1<<20)
		for sc.Scan() {
			var r Record
			if json.Unmarshal(sc.Bytes(), &r) == nil {
				a.prev, a.seq = r.Hash, r.Seq
				a.keep(r)
			}
		}
		f.Close()
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, err
	}
	a.f = f
	return a, nil
}

func (a *Auditor) Close() error { a.mu.Lock(); defer a.mu.Unlock(); return a.f.Close() }

func hashRecord(r Record) string {
	r.Hash = ""
	raw, _ := json.Marshal(r)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func (a *Auditor) Write(r Record) int64 {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.seq++
	r.Seq, r.Prev = a.seq, a.prev
	r.Hash = hashRecord(r)
	a.prev = r.Hash
	line, _ := json.Marshal(r)
	a.f.Write(append(line, '\n'))
	a.keep(r)
	return r.Seq
}

func (a *Auditor) keep(r Record) {
	a.recent = append(a.recent, r)
	if len(a.recent) > 5000 {
		a.recent = a.recent[len(a.recent)-5000:]
	}
}

func (a *Auditor) Query(run string, limit int) []Record {
	a.mu.Lock()
	defer a.mu.Unlock()
	out := []Record{}
	for i := len(a.recent) - 1; i >= 0 && len(out) < limit; i-- {
		if run == "" || a.recent[i].Run == run {
			out = append(out, a.recent[i])
		}
	}
	return out
}

// Verify re-reads the whole file and checks every link of the chain.
func (a *Auditor) Verify() (int64, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	f, err := os.Open(a.path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	prev, n := genesis, int64(0)
	for sc.Scan() {
		var r Record
		if err := json.Unmarshal(sc.Bytes(), &r); err != nil {
			return n, err
		}
		if r.Prev != prev || hashRecord(r) != r.Hash {
			return n, errors.New("chain broken at seq " + itoa(int(r.Seq)))
		}
		prev, n = r.Hash, n+1
	}
	return n, nil
}

var secretField = regexp.MustCompile(`(?i)("(?:password|passwd|secret|token|api_key|apikey|authorization)"\s*:\s*)"[^"]*"`)

// redact masks secret-looking JSON values before a body snippet is written to the log.
func redact(s string) string { return secretField.ReplaceAllString(s, `$1"***"`) }

func snippet(b []byte, n int) string {
	if len(b) > n {
		b = b[:n]
	}
	return redact(string(b))
}

func sha(b []byte) string {
	if len(b) == 0 {
		return ""
	}
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}
