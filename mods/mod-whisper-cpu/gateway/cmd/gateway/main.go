package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"github.com/ovler-young/stt-vault/mod-whisper-cpu-gateway/internal/contract"
	"github.com/ovler-young/stt-vault/mod-whisper-cpu-gateway/internal/gateway"
	"github.com/ovler-young/stt-vault/mod-whisper-cpu-gateway/internal/supervisor"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

type manifest struct {
	Models map[string]supervisor.Model `json:"models"`
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		healthcheck(os.Args[2:])
		return
	}
	serve()
}
func serve() {
	modelID := env("WHISPER_MODEL_ID", "ggml-base.en.bin")
	raw, err := os.ReadFile(env("WHISPER_MANIFEST_PATH", "/app/model-manifest.json"))
	if err != nil {
		panic(err)
	}
	var m manifest
	if err = json.Unmarshal(raw, &m); err != nil {
		panic(err)
	}
	model, ok := m.Models[modelID]
	if !ok {
		panic("WHISPER_MODEL_ID is not declared in model manifest")
	}
	mod := contract.Mod{ID: env("WHISPER_MOD_ID", "mod-whisper-cpu"), Version: env("WHISPER_MOD_VERSION", "0.1.0"), ImageDigest: os.Getenv("WHISPER_IMAGE_DIGEST"), Runtime: "whisper.cpp-cpu", Model: contract.Model(model)}
	engine := supervisor.New(model, env("WHISPER_MODELS_DIR", "/models"), env("WHISPER_SERVER_PORT", "8178"))
	server, err := gateway.New(gateway.Config{TokenPath: env("WHISPER_TOKEN_PATH", "/run/secrets/stt_mod_token"), Mod: mod}, engine)
	if err != nil {
		panic(err)
	}
	server.Start()
	httpServer := &http.Server{
		Addr:              env("WHISPER_GATEWAY_ADDR", ":8081"),
		Handler:           server.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       30 * time.Second,
	}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
	go func() { <-stop; server.Shutdown(); _ = httpServer.Close() }()
	if err = httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		panic(err)
	}
}
func healthcheck(args []string) {
	flags := flag.NewFlagSet("healthcheck", flag.ExitOnError)
	addr := flags.String("addr", "http://127.0.0.1:8081", "loopback gateway URL")
	secret := flags.String("secret", "/run/secrets/stt_mod_token", "secret file")
	flags.Parse(args)
	token, err := os.ReadFile(filepath.Clean(*secret))
	if err != nil {
		fmt.Fprintln(os.Stderr, "cannot read secret")
		os.Exit(1)
	}
	request, err := http.NewRequest(http.MethodGet, *addr+"/readyz", nil)
	if err != nil {
		os.Exit(1)
	}
	request.Header.Set("Authorization", "Bearer "+string(token))
	client := &http.Client{Timeout: 5 * time.Second}
	response, err := client.Do(request)
	if err != nil || response.StatusCode != http.StatusOK {
		if response != nil {
			response.Body.Close()
		}
		os.Exit(1)
	}
	response.Body.Close()
}
func env(k, v string) string {
	if value := os.Getenv(k); value != "" {
		return value
	}
	return v
}
