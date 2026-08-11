package gateway

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/ovler-young/stt-vault/mod-whisper-cpu-gateway/internal/contract"
)

type fakeEngine struct {
	mu        sync.Mutex
	readiness string
	calls     int
	cancelled int
	closed    int
	result    map[string]any
	block     bool
	started   chan struct{}
}

func (f *fakeEngine) Load() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.readiness = "ready"
	return nil
}
func (f *fakeEngine) Restart() error { return f.Load() }
func (f *fakeEngine) CancelActive() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.cancelled++
	f.readiness = "loading"
}
func (f *fakeEngine) Close() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.closed++
	f.readiness = "failed"
}
func (f *fakeEngine) Readiness() string            { f.mu.Lock(); defer f.mu.Unlock(); return f.readiness }
func (f *fakeEngine) Diagnostics() (int, int, int) { return 42, 1, 1 }
func (f *fakeEngine) Transcribe(ctx context.Context, _ []byte) (map[string]any, error) {
	f.mu.Lock()
	f.calls++
	block, started, result := f.block, f.started, f.result
	f.mu.Unlock()
	if block {
		select {
		case started <- struct{}{}:
		default:
		}
		<-ctx.Done()
		return nil, ctx.Err()
	}
	if result != nil {
		return result, nil
	}
	return map[string]any{"text": "transcript"}, nil
}

func TestShutdownCancelsInFlightRequestAndRemovesTemporaryAudio(t *testing.T) {
	server, engine, _ := testServer(t)
	t.Setenv("TMPDIR", t.TempDir())
	engine.block = true
	engine.started = make(chan struct{}, 1)
	body, contentType := requestBody(t, "66666666-6666-4666-8666-666666666666")
	request := httptest.NewRequest(http.MethodPost, "/v1/transcriptions", body)
	request.Header.Set("Authorization", "Bearer secret")
	request.Header.Set("Content-Type", contentType)
	result := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { server.Handler().ServeHTTP(result, request); close(done) }()
	select {
	case <-engine.started:
	case <-time.After(time.Second):
		t.Fatal("inference did not start")
	}
	server.Shutdown()
	select {
	case <-done:
		if result.Code != http.StatusServiceUnavailable {
			t.Fatalf("status=%d", result.Code)
		}
	case <-time.After(time.Second):
		t.Fatal("shutdown did not release request")
	}
	files, err := filepath.Glob(filepath.Join(os.TempDir(), "stt-whisper-*.wav"))
	if err != nil || len(files) != 0 {
		t.Fatalf("temporary audio remains: %v err=%v", files, err)
	}
}

func TestShutdownClosesSlowMultipartUploadAndRemovesPartialAudio(t *testing.T) {
	server, engine, _ := testServer(t)
	t.Setenv("TMPDIR", t.TempDir())
	reader, writer := io.Pipe()
	body := multipart.NewWriter(writer)
	request := httptest.NewRequest(http.MethodPost, "/v1/transcriptions", reader)
	request.Header.Set("Authorization", "Bearer secret")
	request.Header.Set("Content-Type", body.FormDataContentType())
	entered := make(chan struct{})
	release := make(chan struct{})
	writerDone := make(chan struct{})
	go func() {
		defer close(writerDone)
		defer writer.Close()
		requestPart, err := body.CreatePart(map[string][]string{"Content-Disposition": {`form-data; name="request"`}, "Content-Type": {"application/json"}})
		if err != nil {
			return
		}
		if _, err = requestPart.Write([]byte(`{"contract_version":"v1"}`)); err != nil {
			return
		}
		if _, err = body.CreatePart(map[string][]string{"Content-Disposition": {`form-data; name="audio"; filename="chunk.wav"`}, "Content-Type": {"audio/wav"}}); err != nil {
			return
		}
		close(entered)
		<-release
	}()
	result := httptest.NewRecorder()
	handlerDone := make(chan struct{})
	go func() { server.Handler().ServeHTTP(result, request); close(handlerDone) }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("multipart audio upload did not start")
	}
	shutdownDone := make(chan struct{})
	go func() { server.Shutdown(); close(shutdownDone) }()
	select {
	case <-shutdownDone:
	case <-time.After(time.Second):
		t.Fatal("shutdown waited for an incomplete multipart upload")
	}
	engine.mu.Lock()
	closed := engine.closed
	engine.mu.Unlock()
	if closed != 1 {
		t.Fatalf("engine close calls=%d", closed)
	}
	close(release)
	select {
	case <-handlerDone:
	case <-time.After(time.Second):
		t.Fatal("slow multipart handler did not return")
	}
	select {
	case <-writerDone:
	case <-time.After(time.Second):
		t.Fatal("multipart writer remained blocked")
	}
	files, err := filepath.Glob(filepath.Join(os.TempDir(), "stt-whisper-*.wav"))
	if err != nil || len(files) != 0 {
		t.Fatalf("temporary audio remains: %v err=%v", files, err)
	}
}

func TestShutdownCancelsQueuedRequestBeforeItCanRun(t *testing.T) {
	server, engine, _ := testServer(t)
	engine.block, engine.started = true, make(chan struct{}, 1)
	firstBody, firstType := requestBody(t, "77777777-7777-4777-8777-777777777777")
	secondBody, secondType := requestBody(t, "88888888-8888-4888-8888-888888888888")
	results := make(chan *httptest.ResponseRecorder, 2)
	for _, input := range []struct {
		body        *bytes.Buffer
		contentType string
	}{{firstBody, firstType}, {secondBody, secondType}} {
		request := httptest.NewRequest(http.MethodPost, "/v1/transcriptions", input.body)
		request.Header.Set("Authorization", "Bearer secret")
		request.Header.Set("Content-Type", input.contentType)
		result := httptest.NewRecorder()
		go func() { server.Handler().ServeHTTP(result, request); results <- result }()
	}
	select {
	case <-engine.started:
	case <-time.After(time.Second):
		t.Fatal("inference did not start")
	}
	server.Shutdown()
	for range 2 {
		select {
		case result := <-results:
			if result.Code != http.StatusServiceUnavailable {
				t.Fatalf("status=%d", result.Code)
			}
		case <-time.After(time.Second):
			t.Fatal("shutdown left a request blocked")
		}
	}
	if engine.calls != 1 {
		t.Fatalf("queued request started inference: %d calls", engine.calls)
	}
}

func TestTemporaryAdmissionRejectsBeforeSpooling(t *testing.T) {
	server, engine, _ := testServer(t)
	server.config.TempSlots = 1
	server.tempPermit = make(chan struct{}, 1)
	engine.block, engine.started = true, make(chan struct{}, 1)
	body, contentType := requestBody(t, "99999999-9999-4999-8999-999999999999")
	request := httptest.NewRequest(http.MethodPost, "/v1/transcriptions", body)
	request.Header.Set("Authorization", "Bearer secret")
	request.Header.Set("Content-Type", contentType)
	done := make(chan struct{})
	go func() { server.Handler().ServeHTTP(httptest.NewRecorder(), request); close(done) }()
	select {
	case <-engine.started:
	case <-time.After(time.Second):
		t.Fatal("inference did not start")
	}
	body, contentType = requestBody(t, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
	request = httptest.NewRequest(http.MethodPost, "/v1/transcriptions", body)
	request.Header.Set("Authorization", "Bearer secret")
	request.Header.Set("Content-Type", contentType)
	result := httptest.NewRecorder()
	server.Handler().ServeHTTP(result, request)
	if result.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status=%d", result.Code)
	}
	server.Shutdown()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("shutdown did not release admission")
	}
}

func testServer(t *testing.T) (*Server, *fakeEngine, string) {
	t.Helper()
	dir := t.TempDir()
	token := filepath.Join(dir, "token")
	if err := os.WriteFile(token, []byte("secret\n"), 0600); err != nil {
		t.Fatal(err)
	}
	engine := &fakeEngine{readiness: "ready"}
	model := contract.Model{ID: "ggml-base.en.bin", Revision: "v1", SHA256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", LicenseRef: "MIT", AccessDeclaration: "public"}
	server, err := New(Config{TokenPath: token, Mod: contract.Mod{ID: "mod-whisper-cpu", Version: "1.0.0", ImageDigest: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", Runtime: "whisper.cpp-cpu", Model: model}}, engine)
	if err != nil {
		t.Fatal(err)
	}
	return server, engine, token
}
func wav() []byte {
	frames := 800
	b := make([]byte, 44+frames*2)
	copy(b, "RIFF")
	binary.LittleEndian.PutUint32(b[4:], uint32(36+frames*2))
	copy(b[8:], "WAVEfmt ")
	binary.LittleEndian.PutUint32(b[16:], 16)
	binary.LittleEndian.PutUint16(b[20:], 1)
	binary.LittleEndian.PutUint16(b[22:], 1)
	binary.LittleEndian.PutUint32(b[24:], 16000)
	binary.LittleEndian.PutUint32(b[28:], 32000)
	binary.LittleEndian.PutUint16(b[32:], 2)
	binary.LittleEndian.PutUint16(b[34:], 16)
	copy(b[36:], "data")
	binary.LittleEndian.PutUint32(b[40:], uint32(frames*2))
	return b
}
func requestBody(t *testing.T, key string) (*bytes.Buffer, string) {
	return requestBodyWithAudio(t, key, wav())
}

func requestBodyWithAudio(t *testing.T, key string, audio []byte) (*bytes.Buffer, string) {
	t.Helper()
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	request := map[string]any{"contract_version": "v1", "correlation_id": "11111111-1111-4111-8111-111111111111", "idempotency_key": key, "asset_id": "asset:1", "chunk": map[string]any{"index": 0, "start": 0.0, "end": 0.05, "speaker_id": "speaker:1"}, "language": "en", "prompt": nil}
	p, err := writer.CreatePart(map[string][]string{"Content-Disposition": {`form-data; name="request"`}, "Content-Type": {"application/json"}})
	if err != nil {
		t.Fatal(err)
	}
	if err = json.NewEncoder(p).Encode(request); err != nil {
		t.Fatal(err)
	}
	p, err = writer.CreatePart(map[string][]string{"Content-Disposition": {`form-data; name="audio"; filename="chunk.wav"`}, "Content-Type": {"audio/wav"}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = p.Write(audio); err != nil {
		t.Fatal(err)
	}
	if err = writer.Close(); err != nil {
		t.Fatal(err)
	}
	return body, writer.FormDataContentType()
}

func TestMalformedWAVIsRejectedAndTemporaryAudioIsRemoved(t *testing.T) {
	server, _, _ := testServer(t)
	t.Setenv("TMPDIR", t.TempDir())
	invalid := append(wav(), 0)
	body, contentType := requestBodyWithAudio(t, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", invalid)
	request := httptest.NewRequest(http.MethodPost, "/v1/transcriptions", body)
	request.Header.Set("Authorization", "Bearer secret")
	request.Header.Set("Content-Type", contentType)
	result := httptest.NewRecorder()
	server.Handler().ServeHTTP(result, request)
	if result.Code != http.StatusUnprocessableEntity {
		t.Fatalf("status=%d", result.Code)
	}
	files, err := filepath.Glob(filepath.Join(os.TempDir(), "stt-whisper-*.wav"))
	if err != nil || len(files) != 0 {
		t.Fatalf("temporary audio remains: %v err=%v", files, err)
	}
}
func TestAuthReplayAndInputValidation(t *testing.T) {
	server, engine, _ := testServer(t)
	handler := server.Handler()
	unauthorized := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	result := httptest.NewRecorder()
	handler.ServeHTTP(result, unauthorized)
	if result.Code != 401 {
		t.Fatalf("status=%d", result.Code)
	}
	key := "22222222-2222-4222-8222-222222222222"
	body, contentType := requestBody(t, key)
	request := httptest.NewRequest(http.MethodPost, "/v1/transcriptions", body)
	request.Header.Set("Authorization", "Bearer secret")
	request.Header.Set("Content-Type", contentType)
	result = httptest.NewRecorder()
	handler.ServeHTTP(result, request)
	if result.Code != 200 {
		t.Fatalf("status=%d payload=%s", result.Code, result.Body.String())
	}
	body, contentType = requestBody(t, key)
	request = httptest.NewRequest(http.MethodPost, "/v1/transcriptions", body)
	request.Header.Set("Authorization", "Bearer secret")
	request.Header.Set("Content-Type", contentType)
	result = httptest.NewRecorder()
	handler.ServeHTTP(result, request)
	if result.Code != 200 || engine.calls != 1 {
		t.Fatalf("replay status=%d calls=%d", result.Code, engine.calls)
	}
}

func TestStructuredEngineResultRejectsTimedUnitsWithoutCapability(t *testing.T) {
	server, engine, _ := testServer(t)
	engine.result = map[string]any{"result": map[string]any{"kind": "speech", "segments": []map[string]any{{"start": 0.0, "end": 0.05, "text": "x"}}, "timed_units": []map[string]any{{"unit_index": 0, "text": "x", "start_ms": 0, "end_ms": 50, "confidence": nil, "language": "en", "token_kind": "word"}}}}
	body, contentType := requestBody(t, "33333333-3333-4333-8333-333333333333")
	request := httptest.NewRequest(http.MethodPost, "/v1/transcriptions", body)
	request.Header.Set("Authorization", "Bearer secret")
	request.Header.Set("Content-Type", contentType)
	result := httptest.NewRecorder()
	server.Handler().ServeHTTP(result, request)
	if result.Code != http.StatusServiceUnavailable {
		t.Fatalf("status=%d payload=%s", result.Code, result.Body.String())
	}
}

func TestCapabilitiesDeclareConfiguredTimedUnitPrecision(t *testing.T) {
	server, _, _ := testServer(t)
	server.config.TimedUnits = &contract.TimedUnitsCapability{UnitKinds: []string{"word", "punctuation"}, TimeBase: "chunk_ms", PrecisionMS: 10}
	request := httptest.NewRequest(http.MethodGet, "/v1/capabilities", nil)
	request.Header.Set("Authorization", "Bearer secret")
	result := httptest.NewRecorder()
	server.Handler().ServeHTTP(result, request)
	if result.Code != http.StatusOK || !bytes.Contains(result.Body.Bytes(), []byte(`"precision_ms":10`)) {
		t.Fatalf("status=%d payload=%s", result.Code, result.Body.String())
	}
}
