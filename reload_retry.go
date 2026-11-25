package main

import (
	"bufio"
	"bytes"
	"io"
	"net"
	"net/http"
	"time"
)

// A hot reload builds a new server, shuts the old one down and swaps them.
// Requests still waiting in the old one's scheduler (in line for a busy model,
// or for a load) were answered "... is shutting down" even when their model
// carried over to the new server unchanged. serveRetrying hands such a request
// to the new server instead: the old one never started answering it, so it
// can be sent again whole.

const (
	retryBodyLimit = 64 << 20 // a bigger body is passed through, and not retried
	retryMaxTries  = 10       // reloads one request may follow; each waits for the next server
	retrySwapWait  = 60 * time.Second
)

var shuttingDownMark = []byte("is shutting down")

// holdWriter holds back a 500 until its body shows whether it is the old
// server's shutdown answer (then it is dropped, to try again) or a real error.
// Anything else goes straight through.
type holdWriter struct {
	w         http.ResponseWriter
	h         http.Header
	status    int
	committed bool
	retry     bool
}

func newHoldWriter(w http.ResponseWriter) *holdWriter { return &holdWriter{w: w, h: http.Header{}} }

func (hw *holdWriter) Header() http.Header {
	if hw.committed {
		return hw.w.Header()
	}
	return hw.h
}

func (hw *holdWriter) commit() {
	if hw.committed {
		return
	}
	hw.committed = true
	dst := hw.w.Header()
	for k, v := range hw.h {
		dst[k] = v
	}
	if hw.status == 0 {
		hw.status = http.StatusOK
	}
	hw.w.WriteHeader(hw.status)
}

func (hw *holdWriter) WriteHeader(code int) {
	if hw.committed || hw.retry {
		return
	}
	if code >= 100 && code < 200 && code != http.StatusSwitchingProtocols {
		// interim responses go out as they come and leave the final status open
		dst := hw.w.Header()
		for k, v := range hw.h {
			dst[k] = v
		}
		hw.w.WriteHeader(code)
		return
	}
	hw.status = code
	if code != http.StatusInternalServerError {
		hw.commit()
	}
}

func (hw *holdWriter) Write(b []byte) (int, error) {
	if hw.retry {
		return len(b), nil
	}
	if !hw.committed {
		if hw.status == http.StatusInternalServerError && bytes.Contains(b, shuttingDownMark) {
			hw.retry = true
			return len(b), nil
		}
		hw.commit()
	}
	return hw.w.Write(b)
}

// finish sends a held status that was never followed by a body.
func (hw *holdWriter) finish() {
	if !hw.retry && !hw.committed && hw.status != 0 {
		hw.commit()
	}
}

func (hw *holdWriter) Flush() {
	if hw.retry {
		return
	}
	if !hw.committed && hw.status != http.StatusInternalServerError {
		hw.commit()
	}
	if f, ok := hw.w.(http.Flusher); ok && hw.committed {
		f.Flush()
	}
}

func (hw *holdWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if h, ok := hw.w.(http.Hijacker); ok {
		hw.committed = true
		return h.Hijack()
	}
	return nil, nil, http.ErrNotSupported
}

func (hw *holdWriter) Unwrap() http.ResponseWriter { return hw.w }

// serveRetrying serves r on the active server; when the answer is the old
// server's shutdown during a reload, it waits for the new server and sends the
// request there. current returns the active server and its generation, which
// a reload increments.
func serveRetrying(w http.ResponseWriter, r *http.Request, current func() (http.Handler, uint64)) {
	if r.Header.Get("Upgrade") != "" { // websockets: nothing to hold back
		srv, _ := current()
		srv.ServeHTTP(w, r)
		return
	}
	var body []byte
	if r.Body != nil && r.Body != http.NoBody {
		buf, err := io.ReadAll(io.LimitReader(r.Body, retryBodyLimit+1))
		if err != nil || len(buf) > retryBodyLimit {
			r.Body = io.NopCloser(io.MultiReader(bytes.NewReader(buf), r.Body))
			srv, _ := current()
			srv.ServeHTTP(w, r)
			return
		}
		body = buf
	}
	for try := 1; ; try++ {
		srv, gen := current()
		if body != nil {
			r.Body = io.NopCloser(bytes.NewReader(body))
			r.ContentLength = int64(len(body))
		}
		if try == retryMaxTries {
			srv.ServeHTTP(w, r)
			return
		}
		hw := newHoldWriter(w)
		srv.ServeHTTP(hw, r)
		if !hw.retry {
			hw.finish()
			return
		}
		// the old server was shutting down: wait for the new one to take over
		deadline := time.Now().Add(retrySwapWait)
		for time.Now().Before(deadline) {
			if _, now := current(); now != gen {
				break
			}
			select {
			case <-r.Context().Done():
				return
			case <-time.After(50 * time.Millisecond):
			}
		}
	}
}
