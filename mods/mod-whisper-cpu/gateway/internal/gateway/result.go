package gateway

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/ovler-young/stt-vault/mod-whisper-cpu-gateway/internal/contract"
)

// resultFromEngine accepts the two documented whisper-server shapes. Structured
// output is retained verbatim so validators, rather than text reconstruction,
// own all timing semantics.
func resultFromEngine(raw map[string]any, duration float64) (contract.Result, error) {
	if value, ok := raw["result"]; ok {
		encoded, err := json.Marshal(value)
		if err != nil {
			return contract.Result{}, err
		}
		var output contract.Result
		if err := json.Unmarshal(encoded, &output); err != nil {
			return contract.Result{}, err
		}
		return output, nil
	}
	text, ok := raw["text"].(string)
	if !ok {
		return contract.Result{}, fmt.Errorf("engine response did not contain text or result")
	}
	if strings.TrimSpace(text) == "" {
		return contract.Result{Kind: "no_speech", Segments: []contract.Segment{}}, nil
	}
	return contract.Result{Kind: "speech", Segments: []contract.Segment{{Start: 0, End: duration, Text: strings.TrimSpace(text)}}}, nil
}
