package supervisor

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

const defaultInferenceTimeout = 89 * time.Second

type Model struct {
	ID                string `json:"id"`
	Revision          string `json:"revision"`
	SHA256            string `json:"sha256"`
	LicenseRef        string `json:"license_ref"`
	AccessDeclaration string `json:"access_declaration"`
}

type child struct {
	cmd      *exec.Cmd
	exited   chan struct{}
	stopping bool
}

type Supervisor struct {
	mu                                            sync.Mutex
	loadMu                                        sync.Mutex
	model                                         Model
	modelsDir, port, command                      string
	current                                       *child
	generation, loads                             int
	readiness                                     string
	closed                                        bool
	startupClient                                 *http.Client
	startupTimeout, stopTimeout, inferenceTimeout time.Duration
	commandEnv, commandPrefix                     []string
}

func New(model Model, modelsDir, port string) *Supervisor {
	return &Supervisor{model: model, modelsDir: modelsDir, port: port, command: "whisper-server", readiness: "loading", startupClient: &http.Client{Timeout: 2 * time.Second}, startupTimeout: 30 * time.Second, stopTimeout: 10 * time.Second, inferenceTimeout: defaultInferenceTimeout}
}

func (s *Supervisor) Readiness() string { s.mu.Lock(); defer s.mu.Unlock(); return s.readiness }

func (s *Supervisor) Diagnostics() (int, int, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	pid := 0
	if s.current != nil && s.current.cmd.Process != nil {
		pid = s.current.cmd.Process.Pid
	}
	return pid, s.generation, s.loads
}

func (s *Supervisor) Load() error {
	s.loadMu.Lock()
	defer s.loadMu.Unlock()
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return fmt.Errorf("whisper-server is closed")
	}
	if s.current != nil && s.readiness == "ready" {
		s.mu.Unlock()
		return nil
	}
	s.mu.Unlock()
	s.stop()
	path := filepath.Join(s.modelsDir, s.model.ID)
	data, err := os.ReadFile(path)
	if err != nil || fmt.Sprintf("%x", sha256.Sum256(data)) != s.model.SHA256 {
		s.setFailed()
		return fmt.Errorf("selected model is missing or does not match the image manifest")
	}
	args := append(append([]string{}, s.commandPrefix...), "-m", path, "-ng", "-1", "--host", "127.0.0.1", "--port", s.port)
	cmd := exec.Command(s.command, args...)
	if s.commandEnv != nil {
		cmd.Env = s.commandEnv
	}
	cmd.Stdin, cmd.Stdout, cmd.Stderr = nil, io.Discard, io.Discard
	if err = cmd.Start(); err != nil {
		s.setFailed()
		return err
	}
	c := &child{cmd: cmd, exited: make(chan struct{})}
	go s.watch(c)
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		s.terminate(c)
		return fmt.Errorf("whisper-server is closed")
	}
	s.current, s.readiness = c, "loading"
	s.mu.Unlock()
	if err = s.waitReady(c); err != nil {
		s.stop()
		s.setFailed()
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed || s.current != c {
		return fmt.Errorf("whisper-server exited during startup")
	}
	s.generation++
	s.loads++
	s.readiness = "ready"
	return nil
}

func (s *Supervisor) watch(c *child) {
	_ = c.cmd.Wait()
	s.mu.Lock()
	if s.current == c {
		s.current = nil
		if !s.closed && !c.stopping {
			s.readiness = "failed"
		}
	}
	s.mu.Unlock()
	close(c.exited)
}

func (s *Supervisor) waitReady(c *child) error {
	deadline := time.NewTimer(s.startupTimeout)
	defer deadline.Stop()
	tick := time.NewTicker(100 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-c.exited:
			return fmt.Errorf("whisper-server exited during startup")
		case <-deadline.C:
			return fmt.Errorf("whisper-server did not become ready")
		case <-tick.C:
			response, err := s.startupClient.Get("http://127.0.0.1:" + s.port + "/")
			if err == nil {
				response.Body.Close()
				return s.selfCheck()
			}
		}
	}
}

func (s *Supervisor) selfCheck() error {
	var wav bytes.Buffer
	wav.Write([]byte("RIFF$\x01\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x01\x00\x00"))
	wav.Write(make([]byte, 256))
	ctx, cancel := context.WithTimeout(context.Background(), s.startupClient.Timeout)
	defer cancel()
	var output map[string]any
	return s.callJSON(ctx, wav.Bytes(), &output)
}

func (s *Supervisor) Transcribe(ctx context.Context, wav []byte) (map[string]any, error) {
	s.mu.Lock()
	ready := s.readiness == "ready" && !s.closed && s.current != nil
	s.mu.Unlock()
	if !ready {
		return nil, fmt.Errorf("whisper-server is not ready")
	}
	ctx, cancel := s.inferenceContext(ctx)
	defer cancel()
	var payload map[string]any
	err := s.callJSON(ctx, wav, &payload)
	return payload, err
}

func (s *Supervisor) inferenceContext(ctx context.Context) (context.Context, context.CancelFunc) {
	if deadline, ok := ctx.Deadline(); ok && time.Until(deadline) <= s.inferenceTimeout {
		return context.WithCancel(ctx)
	}
	return context.WithTimeout(ctx, s.inferenceTimeout)
}

func (s *Supervisor) callJSON(ctx context.Context, wav []byte, out *map[string]any) error {
	var body bytes.Buffer
	w := multipart.NewWriter(&body)
	part, err := w.CreateFormFile("file", "chunk.wav")
	if err != nil {
		return err
	}
	if _, err = part.Write(wav); err != nil {
		return err
	}
	if err = w.Close(); err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://127.0.0.1:"+s.port+"/inference", &body)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", w.FormDataContentType())
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("whisper-server inference failed")
	}
	return json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(out)
}

func (s *Supervisor) CancelActive() {
	s.mu.Lock()
	if !s.closed {
		s.readiness = "loading"
	}
	s.mu.Unlock()
	s.stop()
}
func (s *Supervisor) Restart() error { return s.Load() }

func (s *Supervisor) Close() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed, s.readiness = true, "failed"
	s.mu.Unlock()
	s.stop()
}

func (s *Supervisor) setFailed() {
	s.mu.Lock()
	if !s.closed {
		s.readiness = "failed"
	}
	s.mu.Unlock()
}

func (s *Supervisor) stop() {
	s.mu.Lock()
	c := s.current
	s.mu.Unlock()
	if c != nil {
		s.terminate(c)
	}
}

func (s *Supervisor) terminate(c *child) {
	if c.cmd.Process == nil {
		return
	}
	s.mu.Lock()
	c.stopping = true
	s.mu.Unlock()
	_ = c.cmd.Process.Signal(syscall.SIGTERM)
	select {
	case <-c.exited:
	case <-time.After(s.stopTimeout):
		_ = c.cmd.Process.Kill()
		<-c.exited
	}
}
