package server

import (
	"bytes"
	"errors"
	"strings"
	"testing"

	"github.com/Wizard815/Argos/internal/logmon"
)

// A refresh that cannot reach Odysseus is one problem. Reporting it on every
// attempt buried the log: the timer fires every RefreshSeconds, and the
// GET /v1/models trigger fires at most every odysseusMinTriggerInterval, and
// each of them logged its own failure.
func TestAFailingRefreshIsReportedOnce(t *testing.T) {
	s := newTestServer(newStubRouter(nil, ""))
	var buf bytes.Buffer
	s.proxylog = logmon.NewWriter(&buf)

	for i := 0; i < 20; i++ {
		s.logRefreshFailure("periodic", errors.New("context deadline exceeded"))
	}

	lines := strings.Count(buf.String(), "refresh failed")
	if lines != 1 {
		t.Fatalf("20 failures produced %d log lines, want 1: %s", lines, buf.String())
	}
	if !strings.Contains(buf.String(), "context deadline exceeded") {
		t.Errorf("the one line should carry the reason: %s", buf.String())
	}
}

func TestTheRecoveryIsReportedOnce(t *testing.T) {
	s := newTestServer(newStubRouter(nil, ""))
	var buf bytes.Buffer
	s.proxylog = logmon.NewWriter(&buf)

	s.logRefreshFailure("periodic", errors.New("boom"))
	// Successes while healthy say nothing at all.
	for i := 0; i < 5; i++ {
		s.logRefreshSuccess("periodic")
	}
	if got := strings.Count(buf.String(), "recovered"); got != 1 {
		t.Fatalf("recovery was reported %d times, want 1: %s", got, buf.String())
	}
}

func TestAHealthyRefreshSaysNothing(t *testing.T) {
	s := newTestServer(newStubRouter(nil, ""))
	var buf bytes.Buffer
	s.proxylog = logmon.NewWriter(&buf)

	for i := 0; i < 10; i++ {
		s.logRefreshSuccess("periodic")
	}
	if buf.Len() != 0 {
		t.Fatalf("a working refresh logged %q, want nothing", buf.String())
	}
}

func TestAFailureAfterARecoveryIsReportedAgain(t *testing.T) {
	s := newTestServer(newStubRouter(nil, ""))
	var buf bytes.Buffer
	s.proxylog = logmon.NewWriter(&buf)

	s.logRefreshFailure("periodic", errors.New("first"))
	s.logRefreshSuccess("periodic")
	s.logRefreshFailure("periodic", errors.New("second"))
	s.logRefreshFailure("periodic", errors.New("second again"))

	out := buf.String()
	if got := strings.Count(out, "refresh failed"); got != 2 {
		t.Fatalf("two separate outages produced %d lines, want 2: %s", got, out)
	}
	if !strings.Contains(out, "first") || !strings.Contains(out, "second") {
		t.Errorf("both reasons should be in the log: %s", out)
	}
}

// The callers share one flag, so a failure seen by the timer keeps the
// /v1/models trigger quiet too - and vice versa.
func TestTheFlagIsSharedBetweenTheCallers(t *testing.T) {
	s := newTestServer(newStubRouter(nil, ""))
	var buf bytes.Buffer
	s.proxylog = logmon.NewWriter(&buf)

	s.logRefreshFailure("startup", errors.New("down"))
	s.logRefreshFailure("background", errors.New("down"))
	s.logRefreshFailure("periodic", errors.New("down"))

	if got := strings.Count(buf.String(), "refresh failed"); got != 1 {
		t.Fatalf("three callers produced %d lines, want 1: %s", got, buf.String())
	}
}
