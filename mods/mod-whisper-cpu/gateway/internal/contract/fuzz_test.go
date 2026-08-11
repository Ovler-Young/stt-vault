package contract

import (
	"encoding/json"
	"testing"
)

func FuzzRequestValidation(f *testing.F) {
	f.Add([]byte(`{"contract_version":"v1"}`))
	f.Fuzz(func(t *testing.T, input []byte) {
		var request Request
		if json.Unmarshal(input, &request) == nil {
			_ = ValidateRequest(request)
		}
	})
}
