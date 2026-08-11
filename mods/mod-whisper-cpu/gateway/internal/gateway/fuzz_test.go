package gateway

import (
	"bytes"
	"net/http"
	"testing"
)

func FuzzMultipartHeaders(f *testing.F) {
	f.Add("multipart/form-data; boundary=x", []byte("--x--\r\n"))
	f.Fuzz(func(t *testing.T, contentType string, body []byte) {
		server, _, _ := testServer(t)
		request := newRequest(http.MethodPost, "/v1/transcriptions", bytes.NewReader(body), contentType)
		_, _, _ = server.parts(request)
	})
}

func newRequest(method, target string, body *bytes.Reader, contentType string) *http.Request {
	request, _ := http.NewRequest(method, target, body)
	request.Header.Set("Content-Type", contentType)
	return request
}
