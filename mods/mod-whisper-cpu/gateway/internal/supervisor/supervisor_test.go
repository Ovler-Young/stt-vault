package supervisor

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

func TestFakeWhisperServer(t *testing.T) {
	if os.Getenv("GO_WANT_FAKE_WHISPER_SERVER") != "1" {
		return
	}
	if os.Getenv("FAKE_WHISPER_MODE") == "exit" {
		os.Exit(3)
	}
	if os.Getenv("FAKE_WHISPER_MODE") == "hang" {
		select {}
	}
	if os.Getenv("FAKE_WHISPER_MODE") == "ignore-term" {
		signal.Ignore(syscall.SIGTERM)
	}
	mux := http.NewServeMux()
	var inferences atomic.Int32
	mux.HandleFunc("/", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("/inference", func(w http.ResponseWriter, _ *http.Request) {
		if os.Getenv("FAKE_WHISPER_MODE") == "self-check-fail" {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		if os.Getenv("FAKE_WHISPER_MODE") == "block-after-self-check" && inferences.Add(1) > 1 {
			select {}
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"text": "fixture transcript"})
	})
	if err := http.ListenAndServe("127.0.0.1:"+os.Getenv("FAKE_WHISPER_PORT"), mux); err != nil {
		os.Exit(4)
	}
}

func newFakeSupervisor(t *testing.T, mode string) *Supervisor {
	t.Helper()
	models := t.TempDir()
	modelBytes := []byte("fake model")
	modelPath := filepath.Join(models, "fixture.bin")
	if err := os.WriteFile(modelPath, modelBytes, 0600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(modelBytes)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	_ = listener.Close()
	s := New(Model{ID: "fixture.bin", Revision: "v1", SHA256: hex.EncodeToString(sum[:]), LicenseRef: "MIT", AccessDeclaration: "public"}, models, strconv.Itoa(port))
	s.command = os.Args[0]
	s.commandPrefix = []string{"-test.run=TestFakeWhisperServer", "--"}
	s.startupTimeout = 300 * time.Millisecond
	s.stopTimeout = 40 * time.Millisecond
	s.startupClient = &http.Client{Timeout: 2 * time.Second}
	s.commandEnv = append(os.Environ(), "GO_WANT_FAKE_WHISPER_SERVER=1", "FAKE_WHISPER_PORT="+strconv.Itoa(port), "FAKE_WHISPER_MODE="+mode)
	return s
}

func TestSupervisorReusesOneProcessForDistinctRequests(t *testing.T) {
	s := newFakeSupervisor(t, "ready")
	defer s.Close()
	if err := s.Load(); err != nil {
		t.Fatal(err)
	}
	pid, generation, loads := s.Diagnostics()
	if _, err := s.Transcribe(t.Context(), []byte("wav")); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Transcribe(t.Context(), []byte("wav")); err != nil {
		t.Fatal(err)
	}
	if gotPID, gotGeneration, gotLoads := s.Diagnostics(); gotPID != pid || gotGeneration != generation || gotLoads != loads || loads != 1 {
		t.Fatalf("process changed: before=%d/%d/%d after=%d/%d/%d", pid, generation, loads, gotPID, gotGeneration, gotLoads)
	}
}

func TestSupervisorStartupFailureAndTimeout(t *testing.T) {
	for _, mode := range []string{"exit", "hang", "self-check-fail"} {
		t.Run(mode, func(t *testing.T) {
			s := newFakeSupervisor(t, mode)
			defer s.Close()
			if s.Load() == nil || s.Readiness() != "failed" {
				t.Fatal("startup failure was accepted")
			}
		})
	}
}

func TestSupervisorCloseEscalatesAndReapsIgnoredTermination(t *testing.T) {
	s := newFakeSupervisor(t, "ignore-term")
	if err := s.Load(); err != nil {
		t.Fatal(err)
	}
	pid, _, _ := s.Diagnostics()
	started := time.Now()
	s.Close()
	if elapsed := time.Since(started); elapsed < s.stopTimeout || elapsed > time.Second {
		t.Fatalf("close duration %s did not exercise bounded escalation", elapsed)
	}
	if got, _, _ := s.Diagnostics(); pid == 0 || got != 0 {
		t.Fatalf("child process was not reaped: before=%d after=%d", pid, got)
	}
}

func TestSupervisorCloseRejectsLaterRestart(t *testing.T) {
	s := newFakeSupervisor(t, "ready")
	if err := s.Load(); err != nil {
		t.Fatal(err)
	}
	s.Close()
	if err := s.Restart(); err == nil {
		t.Fatal("closed supervisor accepted a restart")
	}
	if pid, _, _ := s.Diagnostics(); pid != 0 {
		t.Fatalf("closed supervisor retained pid %d", pid)
	}
}

func TestSupervisorCancellationKillsBlockedChildAndRestarts(t *testing.T) {
	s := newFakeSupervisor(t, "block-after-self-check")
	if err := s.Load(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, err := s.Transcribe(t.Context(), []byte("wav")); done <- err }()
	time.Sleep(30 * time.Millisecond)
	started := time.Now()
	s.CancelActive()
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("cancellation took %s", elapsed)
	}
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("blocked inference succeeded after cancellation")
		}
	case <-time.After(time.Second):
		t.Fatal("blocked inference was not released")
	}
	if s.Readiness() != "loading" {
		t.Fatalf("readiness=%s", s.Readiness())
	}
	s.commandEnv = append(os.Environ(), "GO_WANT_FAKE_WHISPER_SERVER=1", "FAKE_WHISPER_PORT="+s.port, "FAKE_WHISPER_MODE=ready")
	if err := s.Restart(); err != nil || s.Readiness() != "ready" {
		t.Fatalf("restart err=%v readiness=%s", err, s.Readiness())
	}
	s.Close()
}

func TestSupervisorMarksUnexpectedExitFailedAndRestarts(t *testing.T) {
	s := newFakeSupervisor(t, "ready")
	defer s.Close()
	if err := s.Load(); err != nil {
		t.Fatal(err)
	}
	pid, generation, loads := s.Diagnostics()
	if err := syscall.Kill(pid, syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for s.Readiness() != "failed" && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if s.Readiness() != "failed" {
		t.Fatal("ready child exit was not observed")
	}
	if err := s.Load(); err != nil {
		t.Fatal(err)
	}
	_, nextGeneration, nextLoads := s.Diagnostics()
	if nextGeneration != generation+1 || nextLoads != loads+1 {
		t.Fatalf("generation/load=%d/%d", nextGeneration, nextLoads)
	}
}

func TestInferenceDeadlineUsesAdapterBudgetAndCallerDeadline(t *testing.T) {
	s := newFakeSupervisor(t, "ready")
	ctx, cancel := s.inferenceContext(context.Background())
	defer cancel()
	deadline, ok := ctx.Deadline()
	if !ok || time.Until(deadline) < 80*time.Second {
		t.Fatal("inference deadline is shorter than adapter budget")
	}
	outer, stop := context.WithTimeout(context.Background(), time.Second)
	defer stop()
	inner, innerCancel := s.inferenceContext(outer)
	defer innerCancel()
	if deadline, ok = inner.Deadline(); !ok || time.Until(deadline) > 2*time.Second {
		t.Fatal("caller deadline was not preserved")
	}
}
