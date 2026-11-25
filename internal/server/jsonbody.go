package server

import (
	"bytes"
	"io"
	"mime"
	"net/http"
	"strings"

	"github.com/Wizard815/Argos/internal/swaputil"
	"github.com/tidwall/gjson"
)

// requireJSONBody fixes the Content-Type of a JSON endpoint's request. Some
// clients send a JSON body with no Content-Type or with text/plain (curl -d
// sends application/x-www-form-urlencoded). Every later step reads the model
// only from a body labelled JSON, so such a request failed with "no model id
// could be identified". A body that is JSON is relabelled application/json; one
// that is not gets a 400 that says so (llama-swap #1194), as does a body
// labelled JSON that does not parse.
func requireJSONBody(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mt, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if mt == "multipart/form-data" {
			next.ServeHTTP(w, r)
			return
		}
		labelled := mt == "application/json" || strings.HasSuffix(mt, "+json")
		body, err := io.ReadAll(r.Body)
		if err != nil {
			swaputil.SendResponse(w, r, http.StatusBadRequest, "could not read the request body")
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
		trimmed := bytes.TrimSpace(body)
		if len(trimmed) == 0 || !gjson.ValidBytes(trimmed) || trimmed[0] != '{' {
			if labelled {
				// labelled JSON but broken: say so, not "no model" (a 404 read like a missing model)
				swaputil.SendResponse(w, r, http.StatusBadRequest, "the request body is not a valid JSON object")
				return
			}
			swaputil.SendResponse(w, r, http.StatusBadRequest,
				"the request body must be a JSON object (Content-Type: application/json), got "+orNone(mt))
			return
		}
		if !gjson.GetBytes(trimmed, "model").Exists() {
			// the request is wrong, not the server: a 400 that says what is missing, not a 404
			swaputil.SendResponse(w, r, http.StatusBadRequest, "the request has no \"model\": Argos hosts several models, so it needs one")
			return
		}
		r.Header.Set("Content-Type", "application/json")
		next.ServeHTTP(w, r)
	})
}

func orNone(s string) string {
	if s == "" {
		return "no Content-Type"
	}
	return s
}
