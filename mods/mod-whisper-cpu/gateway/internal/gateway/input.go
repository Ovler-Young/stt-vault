package gateway

import (
	"errors"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"

	"github.com/ovler-young/stt-vault/mod-whisper-cpu-gateway/internal/media"
)

func (s *Server) parts(r *http.Request) ([]byte, string, error) {
	mediaType, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mediaType != "multipart/form-data" {
		return nil, "", fmt.Errorf("request body must be multipart form data")
	}
	reader := multipart.NewReader(r.Body, params["boundary"])
	seen := map[string]bool{}
	var requestRaw []byte
	path, success := "", false
	defer func() {
		if !success && path != "" {
			_ = os.Remove(path)
		}
	}()
	for {
		part, readErr := reader.NextPart()
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return nil, "", readErr
		}
		name := part.FormName()
		if seen[name] || (name != "request" && name != "audio") {
			return nil, "", fmt.Errorf("request requires request and audio parts")
		}
		seen[name] = true
		switch name {
		case "request":
			if part.Header.Get("Content-Type") != "application/json" {
				return nil, "", fmt.Errorf("request parts have unsupported media types")
			}
			requestRaw, readErr = io.ReadAll(io.LimitReader(part, media.MaxRequestPart+1))
			if readErr != nil || len(requestRaw) > media.MaxRequestPart {
				return nil, "", fmt.Errorf("request part exceeds limit")
			}
		case "audio":
			if part.Header.Get("Content-Type") != "audio/wav" {
				return nil, "", fmt.Errorf("request parts have unsupported media types")
			}
			file, createErr := os.CreateTemp("", "stt-whisper-*.wav")
			if createErr != nil {
				return nil, "", createErr
			}
			path = file.Name()
			_ = file.Chmod(0600)
			_, readErr = io.Copy(file, io.LimitReader(part, s.config.MaxAudioBytes+1))
			closeErr := file.Close()
			if readErr != nil || closeErr != nil {
				_ = os.Remove(path)
				return nil, "", fmt.Errorf("audio failed validation")
			}
		}
	}
	if !seen["request"] || !seen["audio"] {
		return nil, "", fmt.Errorf("request requires request and audio parts")
	}
	success = true
	return requestRaw, path, nil
}

func (s *Server) errorForInput(w http.ResponseWriter, err error, correlation string) {
	var tooLarge *http.MaxBytesError
	if errors.As(err, &tooLarge) {
		s.writeError(w, 413, "resource_exhausted", "request exceeds the configured size limit", true, correlation)
		return
	}
	s.writeError(w, 422, "invalid_request", "request requires request and audio parts", false, correlation)
}
