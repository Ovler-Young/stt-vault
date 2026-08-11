package contract

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
)

func TestCanonicalSemanticHashMatchesPydanticFieldOrder(t *testing.T) {
	language := "en"
	r := Request{ContractVersion: Version, CorrelationID: "11111111-1111-4111-8111-111111111111", IdempotencyKey: "22222222-2222-4222-8222-222222222222", AssetID: "asset:1", Chunk: Chunk{Index: 0, Start: 0, End: .05, SpeakerID: "speaker:1"}, Language: &language}
	want := `{"contract_version":"v1","correlation_id":"11111111-1111-4111-8111-111111111111","idempotency_key":"22222222-2222-4222-8222-222222222222","asset_id":"asset:1","chunk":{"index":0,"start":0.0,"end":0.05,"speaker_id":"speaker:1"},"language":"en","prompt":null}`
	if got := CanonicalRequestJSON(r); got != want {
		t.Fatalf("canonical JSON = %s", got)
	}
	if got := SemanticHash(r, []byte("wav")); got != "722c0b94017faccd2888a52b0423db38341f87a50c595fbd59b51fdc66d4189a" {
		t.Fatalf("hash = %s", got)
	}
}

func TestCanonicalSemanticHashMatchesPydanticUnicodeOracle(t *testing.T) {
	language := "<&>"
	prompt := "quotes \\\" slash \\\\ control\n\u2028\u2029 e\u0301 Chinese中文"
	r := Request{ContractVersion: Version, CorrelationID: "11111111-1111-4111-8111-111111111111", IdempotencyKey: "22222222-2222-4222-8222-222222222222", AssetID: "asset:1", Chunk: Chunk{Index: 0, Start: 0, End: .05, SpeakerID: "speaker:1"}, Language: &language, Prompt: &prompt}
	if got, want := SemanticHash(r, []byte("RIFF\x00audio")), "365a94b4c08a5b565ba6e75dc946039599f104ed06a8c350b4e588189375181f"; got != want {
		t.Fatalf("hash=%s want=%s json=%s", got, want, CanonicalRequestJSON(r))
	}
	r.Language, r.Prompt = nil, nil
	if got, want := SemanticHash(r, []byte("RIFF\x00audio")), "875140380d83efcfe29c6b842816c33e073e8dadcca7aeeb8a980ec9d0c00d6b"; got != want {
		t.Fatalf("hash=%s want=%s", got, want)
	}
}

func TestRequestRejectsNonV4AndNonFinite(t *testing.T) {
	r := Request{ContractVersion: Version, CorrelationID: "00000000-0000-1000-8000-000000000000", IdempotencyKey: "22222222-2222-4222-8222-222222222222", AssetID: "asset:1", Chunk: Chunk{End: 1, SpeakerID: "speaker:1"}}
	if ValidateRequest(r) == nil {
		t.Fatal("accepted non-v4 correlation ID")
	}
}

func TestTimedUnitsKeepPunctuationOverlapAndZeroDuration(t *testing.T) {
	confidence := .8
	result := Result{Kind: "speech", Segments: []Segment{{Start: 0, End: .05, Text: "hello!"}}, TimedUnits: []TimedUnit{{UnitIndex: 0, Text: "hello", StartMS: 0, EndMS: 50, Confidence: &confidence, Language: ptr("en"), TokenKind: "word"}, {UnitIndex: 1, Text: "!", StartMS: 50, EndMS: 50, TokenKind: "punctuation"}}}
	capability := &TimedUnitsCapability{UnitKinds: []string{"word", "punctuation"}, TimeBase: "chunk_ms", PrecisionMS: 10}
	if err := ValidateTimedResult(result, .05, capability); err != nil {
		t.Fatal(err)
	}
	result.TimedUnits[1].StartMS = 45
	if ValidateTimedResult(result, .05, capability) == nil {
		t.Fatal("accepted an off-grid timestamp")
	}
}

func ptr(value string) *string { return &value }

func TestSharedFixtureContractCases(t *testing.T) {
	raw, err := os.ReadFile("../../../fixtures/gateway-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Request         json.RawMessage `json:"request"`
		TimedCapability json.RawMessage `json:"timed_capability"`
		TimedResponse   struct {
			Result Result `json:"result"`
		} `json:"timed_response"`
		HashCases []struct {
			Request  Request `json:"request"`
			AudioHex string  `json:"audio_hex"`
			SHA256   string  `json:"sha256"`
		} `json:"hash_cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	var request Request
	if err := json.Unmarshal(fixture.Request, &request); err != nil || ValidateRequest(request) != nil {
		t.Fatalf("shared request did not validate: %v", err)
	}
	var capability TimedUnitsCapability
	if err := json.Unmarshal(fixture.TimedCapability, &capability); err != nil {
		t.Fatal(err)
	}
	if err := ValidateTimedResult(fixture.TimedResponse.Result, .05, &capability); err != nil {
		t.Fatal(err)
	}
	for _, testCase := range fixture.HashCases {
		audio, err := hex.DecodeString(testCase.AudioHex)
		if err != nil {
			t.Fatal(err)
		}
		if got := SemanticHash(testCase.Request, audio); got != testCase.SHA256 {
			t.Fatalf("fixture hash=%s want=%s", got, testCase.SHA256)
		}
	}
}
