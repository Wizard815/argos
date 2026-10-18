package swaputil

import (
	"net/http"
	"strings"
)

// What a request is for, as its caller declares it.
//
// The scheduler uses this to keep a decode from sharing compute with a prefill:
// llama.cpp serves both from the same slots, so a prefill running beside a decode
// starves it, and the pair finishes slower than either would alone. Holding one
// until the other drains is the fix, and it can only be done on what a caller
// states - a guess from prompt size would be wrong often enough to be worse than
// doing nothing.
//
// The header is opt-in. A request that declares nothing is never held, so a client
// that knows nothing about this behaves exactly as it did before.
const (
	// IntentPrefill is bulk prompt processing: a long prompt, little output.
	IntentPrefill = "prefill"
	// IntentInteractive is a turn somebody is waiting on.
	IntentInteractive = "interactive"
	// IntentBatch is bulk work nobody is waiting on.
	IntentBatch = "batch"
)

// IntentHeader is the header a caller declares its intent in.
const IntentHeader = "X-Argos-Intent"

// IntentOf reads a request's declared intent, or "" when it declares none or
// declares something unknown. Empty means unspecified, not "probably a decode".
func IntentOf(r *http.Request) string {
	if r == nil {
		return ""
	}
	switch strings.ToLower(strings.TrimSpace(r.Header.Get(IntentHeader))) {
	case IntentPrefill:
		return IntentPrefill
	case IntentInteractive:
		return IntentInteractive
	case IntentBatch:
		return IntentBatch
	}
	return ""
}
