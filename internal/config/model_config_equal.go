package config

import "encoding/json"

// ModelConfigEqual reports whether two model configurations describe the same
// launch. A hot reload uses it to decide which running children it can carry
// over to the replacement router instead of restarting them: restarting a warm
// model drops its prompt cache, and for an edit that only touches a sibling
// entry it is pure loss.
//
// Marshal-and-compare rather than reflect.DeepEqual, so map ordering cannot
// make two identical configurations look different. A marshal failure reports
// "different", which is the conservative direction: the model restarts.
func ModelConfigEqual(a, b ModelConfig) bool {
	ab, err := json.Marshal(a)
	if err != nil {
		return false
	}
	bb, err := json.Marshal(b)
	if err != nil {
		return false
	}
	return string(ab) == string(bb)
}
