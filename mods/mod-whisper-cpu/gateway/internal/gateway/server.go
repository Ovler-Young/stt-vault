package gateway

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/ovler-young/stt-vault/mod-whisper-cpu-gateway/internal/contract"
	"github.com/ovler-young/stt-vault/mod-whisper-cpu-gateway/internal/media"
	"github.com/ovler-young/stt-vault/mod-whisper-cpu-gateway/internal/state"
)

const DefaultMaxAudioBytes int64 = 25 * 1024 * 1024
const DefaultMaxAudioSeconds = 120.0
const DefaultTempSlots = 16

type Engine interface {
	Load() error
	Restart() error
	CancelActive()
	Close()
	Readiness() string
	Diagnostics() (int, int, int)
	Transcribe(context.Context, []byte) (map[string]any, error)
}
type Config struct {
	TokenPath       string
	Mod             contract.Mod
	MaxAudioBytes   int64
	MaxAudioSeconds float64
	TempSlots       int
	TimedUnits      *contract.TimedUnitsCapability
}
type Server struct {
	config          Config
	engine          Engine
	state           *state.Store
	inferencePermit chan struct{}
	tempPermit      chan struct{}
	runCtx          context.Context
	shutdownCancel  context.CancelFunc
	mu              sync.Mutex
	closing         bool
	requests        sync.WaitGroup
	requestBodies   map[uint64]io.Closer
	nextRequestID   uint64
	spawnMu         sync.Mutex
	shutdownOnce    sync.Once
}

func New(config Config, engine Engine) (*Server, error) {
	if err := contract.ValidateMod(config.Mod); err != nil {
		return nil, err
	}
	if config.MaxAudioBytes <= 0 {
		config.MaxAudioBytes = DefaultMaxAudioBytes
	}
	if config.MaxAudioSeconds <= 0 {
		config.MaxAudioSeconds = DefaultMaxAudioSeconds
	}
	if config.TempSlots <= 0 {
		config.TempSlots = DefaultTempSlots
	}
	if config.TimedUnits != nil {
		if err := contract.ValidateTimedUnitsCapability(*config.TimedUnits); err != nil {
			return nil, err
		}
	}
	runCtx, cancel := context.WithCancel(context.Background())
	return &Server{config: config, engine: engine, state: state.New(), inferencePermit: make(chan struct{}, 1), tempPermit: make(chan struct{}, config.TempSlots), runCtx: runCtx, shutdownCancel: cancel, requestBodies: make(map[uint64]io.Closer)}, nil
}
func (s *Server) Start() { go func() { _ = s.loadEngine() }() }
func (s *Server) Shutdown() {
	s.shutdownOnce.Do(func() {
		s.mu.Lock()
		s.closing = true
		bodies := make([]io.Closer, 0, len(s.requestBodies))
		for _, body := range s.requestBodies {
			bodies = append(bodies, body)
		}
		s.mu.Unlock()
		s.shutdownCancel()
		s.engine.CancelActive()
		for _, body := range bodies {
			_ = body.Close()
		}
		s.requests.Wait()
		s.spawnMu.Lock()
		s.engine.Close()
		s.spawnMu.Unlock()
	})
}

func (s *Server) isClosing() bool { s.mu.Lock(); defer s.mu.Unlock(); return s.closing }

func (s *Server) loadEngine() error {
	s.spawnMu.Lock()
	defer s.spawnMu.Unlock()
	if s.isClosing() {
		return context.Canceled
	}
	return s.engine.Load()
}

func (s *Server) restartEngine() {
	s.spawnMu.Lock()
	defer s.spawnMu.Unlock()
	if !s.isClosing() {
		_ = s.engine.Restart()
	}
}
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /livez", s.livez)
	mux.HandleFunc("GET /readyz", s.auth(s.readyz))
	mux.HandleFunc("GET /v1/capabilities", s.auth(s.capabilities))
	mux.HandleFunc("POST /v1/transcriptions", s.auth(s.transcribe))
	mux.HandleFunc("POST /v1/cancellations/{idempotency_key}", s.auth(s.cancel))
	return mux
}

func (s *Server) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		expected, err := os.ReadFile(s.config.TokenPath)
		candidate := ""
		if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
			candidate = strings.TrimPrefix(h, "Bearer ")
		}
		expectedToken := strings.TrimSpace(string(expected))
		expectedDigest := sha256.Sum256([]byte(expectedToken))
		candidateDigest := sha256.Sum256([]byte(candidate))
		if err != nil || expectedToken == "" || subtle.ConstantTimeCompare(candidateDigest[:], expectedDigest[:]) != 1 {
			s.writeError(w, http.StatusUnauthorized, "invalid_request", "mod authentication failed", false, "")
			return
		}
		next(w, r)
	}
}
func (s *Server) livez(w http.ResponseWriter, _ *http.Request) {
	s.writeJSON(w, http.StatusOK, map[string]string{"status": "live"})
}
func (s *Server) readyz(w http.ResponseWriter, _ *http.Request) {
	switch s.engine.Readiness() {
	case "ready":
		s.writeJSON(w, http.StatusOK, map[string]any{"status": "ready", "model": map[string]string{"id": s.config.Mod.Model.ID, "revision": s.config.Mod.Model.Revision, "sha256": s.config.Mod.Model.SHA256}, "rss_mb": rssMB()})
	case "loading":
		s.writeError(w, http.StatusServiceUnavailable, "not_ready", "model is loading", true, "")
	default:
		s.writeError(w, http.StatusServiceUnavailable, "provider_failure", "model failed to load", false, "")
	}
}
func (s *Server) capabilities(w http.ResponseWriter, _ *http.Request) {
	result := map[string]any{"offerings": []map[string]string{{"model_id": s.config.Mod.Model.ID, "device_id": "cpu"}}, "max_audio_bytes": s.config.MaxAudioBytes, "max_audio_seconds": s.config.MaxAudioSeconds, "readiness": s.engine.Readiness()}
	if s.config.TimedUnits != nil {
		result["transcription"] = map[string]any{"timed_units": s.config.TimedUnits}
	}
	s.writeJSON(w, http.StatusOK, map[string]any{"contract_version": "v1", "correlation_id": newUUID(), "mod": s.config.Mod, "result": result})
}
func (s *Server) cancel(w http.ResponseWriter, r *http.Request) {
	key := r.PathValue("idempotency_key")
	if !uuid4(key) {
		s.writeError(w, http.StatusUnprocessableEntity, "invalid_request", "request body failed validation", false, "")
		return
	}
	active, done := s.state.Cancel(key)
	if active {
		s.engine.CancelActive()
		go s.restartEngine()
	}
	if done != nil {
		<-done
	}
	w.WriteHeader(http.StatusNoContent)
}
func (s *Server) transcribe(w http.ResponseWriter, r *http.Request) {
	correlation := ""
	requestID, admitted := s.beginRequest(r.Body)
	if !admitted {
		s.writeError(w, http.StatusServiceUnavailable, "unavailable", "gateway is shutting down", true, correlation)
		return
	}
	defer s.endRequest(requestID)
	if !s.acquireTemp(r.Context()) {
		if s.isClosing() {
			s.writeError(w, http.StatusServiceUnavailable, "unavailable", "gateway is shutting down", true, correlation)
		} else {
			s.writeError(w, http.StatusRequestEntityTooLarge, "resource_exhausted", "gateway temporary upload capacity is exhausted", true, correlation)
		}
		return
	}
	defer func() { <-s.tempPermit }()
	r.Body = http.MaxBytesReader(w, r.Body, s.config.MaxAudioBytes)
	requestRaw, audioPath, err := s.parts(r)
	if err != nil {
		s.errorForInput(w, err, correlation)
		return
	}
	defer os.Remove(audioPath)
	var req contract.Request
	if err = json.Unmarshal(requestRaw, &req); err != nil || contract.ValidateRequest(req) != nil {
		s.writeError(w, http.StatusUnprocessableEntity, "invalid_request", "request body failed validation", false, correlation)
		return
	}
	correlation = req.CorrelationID
	duration, err := media.ValidateWAV(audioPath, s.config.MaxAudioSeconds)
	if err != nil {
		if errors.Is(err, media.ErrDuration) {
			s.writeError(w, http.StatusRequestEntityTooLarge, "resource_exhausted", "audio exceeds the configured duration limit", true, correlation)
		} else {
			s.writeError(w, http.StatusUnprocessableEntity, "invalid_request", "audio or response failed validation", false, correlation)
		}
		return
	}
	planned := req.Chunk.End - req.Chunk.Start
	if abs(duration-planned) > 0.050 {
		s.writeError(w, http.StatusUnprocessableEntity, "invalid_request", "audio duration did not match the planned chunk", false, correlation)
		return
	}
	audio, err := os.ReadFile(audioPath)
	if err != nil {
		s.writeError(w, 503, "provider_failure", "transcription engine failed", false, correlation)
		return
	}
	hash := contract.SemanticHash(req, audio)
	existing, begin, done := s.state.Begin(req.IdempotencyKey, hash)
	switch begin {
	case state.Replay:
		s.writeBytes(w, http.StatusOK, existing.Response)
		return
	case state.Mismatch:
		s.writeError(w, http.StatusUnprocessableEntity, "invalid_request", "idempotency key request mismatch", false, correlation)
		return
	case state.Cancelled:
		s.writeError(w, http.StatusServiceUnavailable, "provider_failure", "request was cancelled", false, correlation)
		return
	case state.Pending:
		select {
		case <-done:
		case <-r.Context().Done():
			s.writeError(w, http.StatusServiceUnavailable, "unavailable", "gateway is shutting down", true, correlation)
			return
		case <-s.runCtx.Done():
			s.writeError(w, http.StatusServiceUnavailable, "unavailable", "gateway is shutting down", true, correlation)
			return
		}
		existing, resolved := s.state.Resolve(req.IdempotencyKey, hash)
		if resolved == state.Replay {
			s.writeBytes(w, http.StatusOK, existing.Response)
			return
		}
		if resolved == state.Mismatch {
			s.writeError(w, http.StatusUnprocessableEntity, "invalid_request", "idempotency key request mismatch", false, correlation)
			return
		}
		s.writeError(w, http.StatusServiceUnavailable, "provider_failure", "transcription engine failed", false, correlation)
		return
	}
	if !s.acquireInference(r.Context()) {
		s.state.Finish(req.IdempotencyKey, hash, nil, done, false)
		s.writeError(w, http.StatusServiceUnavailable, "unavailable", "gateway is shutting down", true, correlation)
		return
	}
	defer func() { <-s.inferencePermit }()
	if !s.state.Activate(req.IdempotencyKey, done) {
		s.state.Finish(req.IdempotencyKey, hash, nil, done, false)
		s.writeError(w, 503, "provider_failure", "request was cancelled", false, correlation)
		return
	}
	response, complete := s.run(r.Context(), req, audio, planned)
	payload, marshalErr := json.Marshal(response)
	if marshalErr != nil {
		complete = false
	}
	s.state.Finish(req.IdempotencyKey, hash, payload, done, complete)
	if !complete {
		s.writeError(w, 503, "provider_failure", "transcription engine failed", false, correlation)
		return
	}
	pid, generation, loads := s.engine.Diagnostics()
	w.Header().Set("X-Mod-Engine-Pid", strconv.Itoa(pid))
	w.Header().Set("X-Mod-Engine-Generation", strconv.Itoa(generation))
	w.Header().Set("X-Mod-Engine-Load-Count", strconv.Itoa(loads))
	s.writeBytes(w, 200, payload)
}
func (s *Server) beginRequest(body io.Closer) (uint64, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closing {
		return 0, false
	}
	s.requests.Add(1)
	s.nextRequestID++
	requestID := s.nextRequestID
	s.requestBodies[requestID] = body
	return requestID, true
}

func (s *Server) endRequest(requestID uint64) {
	s.mu.Lock()
	delete(s.requestBodies, requestID)
	s.mu.Unlock()
	s.requests.Done()
}

func (s *Server) acquireTemp(ctx context.Context) bool {
	if s.runCtx.Err() != nil || ctx.Err() != nil {
		return false
	}
	select {
	case s.tempPermit <- struct{}{}:
		return true
	default:
		return false
	case <-ctx.Done():
		return false
	case <-s.runCtx.Done():
		return false
	}
}

func (s *Server) acquireInference(ctx context.Context) bool {
	if s.runCtx.Err() != nil || ctx.Err() != nil {
		return false
	}
	select {
	case s.inferencePermit <- struct{}{}:
		return true
	case <-ctx.Done():
		return false
	case <-s.runCtx.Done():
		return false
	}
}

func (s *Server) run(requestCtx context.Context, req contract.Request, audio []byte, duration float64) (any, bool) {
	if s.isClosing() {
		return nil, false
	}
	if s.engine.Readiness() != "ready" {
		if s.loadEngine() != nil {
			return nil, false
		}
	}
	ctx, cancel := context.WithCancel(requestCtx)
	stop := context.AfterFunc(s.runCtx, cancel)
	defer func() { stop(); cancel() }()
	result, err := s.engine.Transcribe(ctx, audio)
	if err != nil || s.state.Cancelled(req.IdempotencyKey) {
		return nil, false
	}
	output, err := resultFromEngine(result, duration)
	if err != nil || contract.ValidateTimedResult(output, duration, s.config.TimedUnits) != nil {
		return nil, false
	}
	return contract.Response{ContractVersion: "v1", CorrelationID: req.CorrelationID, Mod: s.config.Mod, Result: output}, true
}
func (s *Server) writeError(w http.ResponseWriter, status int, category, message string, retry bool, correlation string) {
	if correlation == "" {
		correlation = newUUID()
	}
	s.writeJSON(w, status, contract.Error{ContractVersion: "v1", CorrelationID: correlation, Mod: s.config.Mod, Error: contract.ErrorDetail{Category: category, Message: message, Retryable: retry}})
}
func (s *Server) writeJSON(w http.ResponseWriter, status int, v any) {
	b, _ := json.Marshal(v)
	s.writeBytes(w, status, b)
}
func (s *Server) writeBytes(w http.ResponseWriter, status int, b []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(b)
}
func rssMB() float64 {
	b, err := os.ReadFile("/proc/self/statm")
	if err != nil {
		return 0
	}
	fields := strings.Fields(string(b))
	if len(fields) < 2 {
		return 0
	}
	pages, _ := strconv.ParseFloat(fields[1], 64)
	return pages * float64(os.Getpagesize()) / (1024 * 1024)
}
func uuid4(s string) bool {
	if len(s) != 36 {
		return false
	}
	for i, c := range s {
		if i == 8 || i == 13 || i == 18 || i == 23 {
			continue
		}
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	for _, i := range []int{8, 13, 18, 23} {
		if s[i] != '-' {
			return false
		}
	}
	return s[14] == '4' && (s[19] == '8' || s[19] == '9' || s[19] == 'a' || s[19] == 'b')
}
func newUUID() string {
	bytes := make([]byte, 16)
	if _, err := rand.Read(bytes); err != nil {
		return fmt.Sprintf("00000000-0000-4000-8000-%012d", time.Now().UnixNano()%1_000_000_000_000)
	}
	bytes[6] = bytes[6]&0x0f | 0x40
	bytes[8] = bytes[8]&0x3f | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", bytes[0:4], bytes[4:6], bytes[6:8], bytes[8:10], bytes[10:16])
}
func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}
