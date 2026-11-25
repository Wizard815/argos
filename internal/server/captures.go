package server

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/Wizard815/Argos/internal/store"
	"github.com/fxamacker/cbor/v2"
	"github.com/klauspost/compress/zstd"
)

// ReqRespCapture is a stored request/response pair for a single metered request.
type ReqRespCapture struct {
	ID          int               `json:"id"`
	ReqPath     string            `json:"req_path"`
	ReqHeaders  map[string]string `json:"req_headers"`
	ReqBody     []byte            `json:"req_body"`
	RespHeaders map[string]string `json:"resp_headers"`
	RespBody    []byte            `json:"resp_body"`
}

// captureFields is a bitmask controlling what a route stores in a ReqRespCapture.
type captureFields uint

const (
	captureReqHeaders captureFields = 1 << iota
	captureReqBody
	captureRespHeaders
	captureRespBody
)

const (
	captureReqAll  = captureReqHeaders | captureReqBody
	captureRespAll = captureRespHeaders | captureRespBody
	captureAll     = captureReqAll | captureRespAll
)

// captureFieldsByPath overrides the default capture mask for routes carrying
// large binary payloads (audio/image) where storing the full body is wasteful.
var captureFieldsByPath = map[string]captureFields{
	"/v1/audio/speech":         captureReqAll | captureRespHeaders,
	"/v1/audio/voices":         captureReqHeaders | captureRespAll,
	"/v1/audio/transcriptions": captureReqHeaders | captureRespHeaders | captureRespBody,
	"/v1/images/generations":   captureReqAll | captureRespHeaders,
	"/v1/images/edits":         captureReqHeaders | captureRespHeaders,
	"/sdapi/v1/txt2img":        captureReqAll | captureRespHeaders,
	"/sdapi/v1/img2img":        captureReqHeaders | captureRespHeaders,
}

// captureFieldsFor returns the capture mask for a request path. Unlisted routes
// (the OpenAI-compatible JSON endpoints) capture everything.
func captureFieldsFor(path string) captureFields {
	if cf, ok := captureFieldsByPath[path]; ok {
		return cf
	}
	return captureAll
}

// zstdEncOptions are the shared zstd encoder options for maximum compression.
var zstdEncOptions = []zstd.EOption{
	zstd.WithEncoderLevel(zstd.SpeedBetterCompression),
}

// zstdEncPool pools zstd.Encoder instances to reduce allocations.
var zstdEncPool = &sync.Pool{
	New: func() interface{} {
		enc, _ := zstd.NewWriter(nil, zstdEncOptions...)
		return enc
	},
}

// zstdDecPool pools zstd.Decoder instances to reduce allocations.
var zstdDecPool = &sync.Pool{
	New: func() interface{} {
		dec, _ := zstd.NewReader(nil)
		return dec
	},
}

// compressCapture marshals a ReqRespCapture to CBOR and compresses it with zstd.
// Returns the compressed bytes and the original CBOR byte count for logging.
func compressCapture(c *ReqRespCapture) ([]byte, int, error) {
	cborBytes, err := cbor.Marshal(c)
	if err != nil {
		return nil, 0, fmt.Errorf("marshal capture: %w", err)
	}
	zenc := zstdEncPool.Get().(*zstd.Encoder)
	defer zstdEncPool.Put(zenc)
	return zenc.EncodeAll(cborBytes, nil), len(cborBytes), nil
}

// decompressCapture decompresses zstd-compressed CBOR into a ReqRespCapture.
func decompressCapture(data []byte) (*ReqRespCapture, error) {
	dec := zstdDecPool.Get().(*zstd.Decoder)
	defer zstdDecPool.Put(dec)
	cborBytes, err := dec.DecodeAll(data, nil)
	if err != nil {
		return nil, fmt.Errorf("decompress capture: %w", err)
	}
	var capture ReqRespCapture
	if err := cbor.Unmarshal(cborBytes, &capture); err != nil {
		return nil, fmt.Errorf("unmarshal capture: %w", err)
	}
	return &capture, nil
}

// addCapture compresses and stores a capture in the cache. Returns true if the
// capture was stored.
func (mp *metricsMonitor) addCapture(capture ReqRespCapture) bool {
	if !mp.enableCaptures || mp.captureCache == nil {
		return false
	}

	compressed, uncompressedBytes, err := compressCapture(&capture)
	if err != nil {
		mp.logger.Warnf("failed to compress capture: %v, skipping", err)
		return false
	}

	if err := mp.captureCache.Add(capture.ID, compressed); err != nil {
		mp.logger.Warnf("capture %d too large (%d bytes), skipping: %v", capture.ID, len(compressed), err)
		return false
	}

	compressionRatio := (1 - float64(len(compressed))/float64(uncompressedBytes)) * 100
	mp.logger.Debugf("Capture %d compressed and saved: %d bytes -> %d bytes (%.1f%% compression)", capture.ID, uncompressedBytes, len(compressed), compressionRatio)
	return true
}

// getCaptureByID returns a capture by ID from the in-memory cache, falling
// back to the store when captures are persisted (so captures survive a
// restart). Returns nil if the capture is not found or cannot be read.
func (mp *metricsMonitor) getCaptureByID(id int) *ReqRespCapture {
	if mp.captureCache != nil {
		if data, err := mp.captureCache.Get(id); err == nil {
			capture, err := decompressCapture(data)
			if err == nil {
				return capture
			}
			mp.warnf("failed to decompress capture %d: %v", id, err)
		}
	}
	if !mp.persistCaptures {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	rec, found, err := mp.store.GetCapture(ctx, id)
	if err != nil {
		mp.warnf("failed to read persisted capture %d: %v", id, err)
		return nil
	}
	if !found {
		return nil
	}
	return &ReqRespCapture{
		ID:          rec.ID,
		ReqPath:     rec.ReqPath,
		ReqHeaders:  rec.ReqHeaders,
		ReqBody:     nilIfEmpty(store.BodyBytes(rec.ReqBody)),
		RespHeaders: rec.RespHeaders,
		RespBody:    nilIfEmpty(store.BodyBytes(rec.RespBody)),
	}
}

func nilIfEmpty(b []byte) []byte {
	if len(b) == 0 {
		return nil
	}
	return b
}

// sensitiveHeaders lists headers that are redacted in captures, both the
// in-memory ones and those written to the store.
var sensitiveHeaders = map[string]bool{
	"authorization":        true,
	"proxy-authorization":  true,
	"cookie":               true,
	"set-cookie":           true,
	"x-api-key":            true,
	"api-key":              true, // Azure OpenAI
	"x-goog-api-key":       true, // Gemini
	"x-auth-token":         true,
	"x-access-token":       true,
	"x-amz-security-token": true,
}

// headerMap flattens an http.Header to a single-value map.
func headerMap(h http.Header) map[string]string {
	m := make(map[string]string, len(h))
	for key, values := range h {
		if len(values) > 0 {
			m[key] = values[0]
		}
	}
	return m
}

// redactHeaders replaces sensitive header values in-place with "[REDACTED]".
func redactHeaders(headers map[string]string) {
	for key := range headers {
		if isSensitiveHeader(key) {
			headers[key] = "[REDACTED]"
		}
	}
}

// isSensitiveHeader reports whether a header carries a credential. Besides the
// fixed list it catches the usual naming patterns of vendor and homelab auth
// headers (X-Foo-Token, X-Foo-Secret, X-Foo-Api-Key), since captures can now
// be written to disk and read by other services.
func isSensitiveHeader(key string) bool {
	k := strings.ToLower(key)
	if sensitiveHeaders[k] {
		return true
	}
	return strings.HasSuffix(k, "-token") ||
		strings.Contains(k, "secret") ||
		strings.Contains(k, "api-key") ||
		strings.Contains(k, "apikey") ||
		strings.Contains(k, "password")
}
