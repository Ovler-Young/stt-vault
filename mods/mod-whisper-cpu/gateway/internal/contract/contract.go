package contract

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

const Version = "v1"

var (
	idRE     = regexp.MustCompile(`^[A-Za-z0-9._:-]+$`)
	semverRE = regexp.MustCompile(`^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$`)
	digestRE = regexp.MustCompile(`^sha256:[a-f0-9]{64}$`)
	shaRE    = regexp.MustCompile(`^[a-f0-9]{64}$`)
	uuid4RE  = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
)

type Model struct {
	ID                string `json:"id"`
	Revision          string `json:"revision"`
	SHA256            string `json:"sha256"`
	LicenseRef        string `json:"license_ref"`
	AccessDeclaration string `json:"access_declaration"`
}
type Mod struct {
	ID          string `json:"id"`
	Version     string `json:"version"`
	ImageDigest string `json:"image_digest"`
	Runtime     string `json:"runtime"`
	Model       Model  `json:"model"`
}
type Chunk struct {
	Index     int     `json:"index"`
	Start     float64 `json:"start"`
	End       float64 `json:"end"`
	SpeakerID string  `json:"speaker_id"`
}
type Request struct {
	ContractVersion string  `json:"contract_version"`
	CorrelationID   string  `json:"correlation_id"`
	IdempotencyKey  string  `json:"idempotency_key"`
	AssetID         string  `json:"asset_id"`
	Chunk           Chunk   `json:"chunk"`
	Language        *string `json:"language"`
	Prompt          *string `json:"prompt"`
}
type Segment struct {
	Start float64 `json:"start"`
	End   float64 `json:"end"`
	Text  string  `json:"text"`
}
type Result struct {
	Kind       string      `json:"kind"`
	Segments   []Segment   `json:"segments"`
	TimedUnits []TimedUnit `json:"timed_units,omitempty"`
}
type TimedUnit struct {
	UnitIndex  int      `json:"unit_index"`
	Text       string   `json:"text"`
	StartMS    int      `json:"start_ms"`
	EndMS      int      `json:"end_ms"`
	Confidence *float64 `json:"confidence"`
	Language   *string  `json:"language"`
	TokenKind  string   `json:"token_kind"`
}
type TimedUnitsCapability struct {
	UnitKinds   []string `json:"unit_kinds"`
	TimeBase    string   `json:"time_base"`
	PrecisionMS int      `json:"precision_ms"`
}
type Response struct {
	ContractVersion string `json:"contract_version"`
	CorrelationID   string `json:"correlation_id"`
	Mod             Mod    `json:"mod"`
	Result          Result `json:"result"`
}
type Error struct {
	ContractVersion string      `json:"contract_version"`
	CorrelationID   string      `json:"correlation_id"`
	Mod             Mod         `json:"mod"`
	Error           ErrorDetail `json:"error"`
}
type ErrorDetail struct {
	Category  string `json:"category"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable"`
}

func ValidateMod(mod Mod) error {
	if !idRE.MatchString(mod.ID) || !semverRE.MatchString(mod.Version) || !digestRE.MatchString(mod.ImageDigest) || mod.ImageDigest == "sha256:"+strings.Repeat("0", 64) || mod.Runtime == "" {
		return fmt.Errorf("invalid mod identity")
	}
	if !idRE.MatchString(mod.Model.ID) || mod.Model.Revision == "" || !shaRE.MatchString(mod.Model.SHA256) || mod.Model.LicenseRef == "" || mod.Model.AccessDeclaration == "" {
		return fmt.Errorf("invalid model identity")
	}
	return nil
}

func ValidateRequest(r Request) error {
	if r.ContractVersion != Version || !uuid4RE.MatchString(r.CorrelationID) || !uuid4RE.MatchString(r.IdempotencyKey) || !idRE.MatchString(r.AssetID) || r.Chunk.Index < 0 || !idRE.MatchString(r.Chunk.SpeakerID) {
		return fmt.Errorf("request body failed validation")
	}
	if !finite(r.Chunk.Start) || !finite(r.Chunk.End) || r.Chunk.Start >= r.Chunk.End {
		return fmt.Errorf("request body failed validation")
	}
	return nil
}

func ValidateResult(result Result, duration float64) error {
	if result.Kind != "speech" && result.Kind != "no_speech" {
		return fmt.Errorf("response failed validation")
	}
	if result.Kind == "no_speech" && len(result.Segments) != 0 {
		return fmt.Errorf("response failed validation")
	}
	var end float64
	for _, segment := range result.Segments {
		if !finite(segment.Start) || !finite(segment.End) || segment.Start < 0 || segment.Start >= segment.End || segment.End > duration+0.050 || strings.TrimSpace(segment.Text) == "" || segment.Start < end {
			return fmt.Errorf("response failed validation")
		}
		end = segment.End
	}
	return nil
}

func ValidateTimedResult(result Result, duration float64, capability *TimedUnitsCapability) error {
	if err := ValidateResult(result, duration); err != nil {
		return err
	}
	if capability == nil {
		if result.TimedUnits != nil {
			return fmt.Errorf("timed units require an advertised capability")
		}
		return nil
	}
	if err := ValidateTimedUnitsCapability(*capability); err != nil {
		return err
	}
	if result.Kind == "speech" && result.TimedUnits == nil {
		return fmt.Errorf("timed capability requires units for speech results")
	}
	if result.Kind == "no_speech" && len(result.TimedUnits) > 0 {
		return fmt.Errorf("no_speech results must not include timed units")
	}
	durationMS := int(math.Floor(duration*1000 + .5))
	for index, unit := range result.TimedUnits {
		if unit.UnitIndex != index || unit.Text == "" || unit.StartMS < 0 || unit.StartMS > unit.EndMS || unit.EndMS > durationMS || unit.StartMS%capability.PrecisionMS != 0 || unit.EndMS%capability.PrecisionMS != 0 || !contains(capability.UnitKinds, unit.TokenKind) || (unit.Confidence != nil && (!finite(*unit.Confidence) || *unit.Confidence < 0 || *unit.Confidence > 1)) || (unit.Language != nil && !bcp47RE.MatchString(*unit.Language)) {
			return fmt.Errorf("timed unit failed validation")
		}
		if index > 0 && unit.StartMS < result.TimedUnits[index-1].StartMS {
			return fmt.Errorf("timed units must be ordered by start")
		}
	}
	return nil
}

func ValidateTimedUnitsCapability(capability TimedUnitsCapability) error {
	if capability.TimeBase != "chunk_ms" || capability.PrecisionMS <= 0 || len(capability.UnitKinds) == 0 {
		return fmt.Errorf("invalid timed-units capability")
	}
	seen := map[string]bool{}
	for _, kind := range capability.UnitKinds {
		if !contains([]string{"word", "token", "punctuation", "other"}, kind) || seen[kind] {
			return fmt.Errorf("invalid timed-units capability")
		}
		seen[kind] = true
	}
	return nil
}

func contains(values []string, value string) bool {
	for _, candidate := range values {
		if candidate == value {
			return true
		}
	}
	return false
}

func SemanticHash(r Request, audio []byte) string {
	// The Python oracle hashes Pydantic's compact field-ordered JSON plus WAV bytes.
	b := []byte(CanonicalRequestJSON(r))
	b = append(b, audio...)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func CanonicalRequestJSON(r Request) string {
	quote := func(v string) string {
		var b bytes.Buffer
		encoder := json.NewEncoder(&b)
		encoder.SetEscapeHTML(false)
		_ = encoder.Encode(v)
		return strings.TrimSuffix(strings.ReplaceAll(strings.ReplaceAll(b.String(), `\u2028`, "\u2028"), `\u2029`, "\u2029"), "\n")
	}
	nullable := func(v *string) string {
		if v == nil {
			return "null"
		}
		return quote(*v)
	}
	return `{"contract_version":` + quote(r.ContractVersion) + `,"correlation_id":` + quote(r.CorrelationID) + `,"idempotency_key":` + quote(r.IdempotencyKey) + `,"asset_id":` + quote(r.AssetID) + `,"chunk":{"index":` + strconv.Itoa(r.Chunk.Index) + `,"start":` + pythonFloat(r.Chunk.Start) + `,"end":` + pythonFloat(r.Chunk.End) + `,"speaker_id":` + quote(r.Chunk.SpeakerID) + `},"language":` + nullable(r.Language) + `,"prompt":` + nullable(r.Prompt) + `}`
}

func pythonFloat(v float64) string {
	s := strconv.FormatFloat(v, 'g', -1, 64)
	if !strings.ContainsAny(s, ".eE") {
		return s + ".0"
	}
	return s
}

var bcp47RE = regexp.MustCompile(`^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$`)

func finite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }
