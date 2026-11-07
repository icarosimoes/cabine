#!/usr/bin/env bash
# Compila o agente para Windows (amd64) dentro do Docker; saída em dist/. Uso: ./build.sh [versão]
set -euo pipefail
cd "$(dirname "$0")"
VERSION="${1:-$(tr -d '[:space:]' < VERSION)}"  # versão única em agent/VERSION (o CI usa a mesma)
mkdir -p dist
docker run --rm -v "$PWD":/src -w /src -v cabine-gomod:/go/pkg/mod golang:1.25 sh -c "
  gofmt -w . && go vet ./... && go test ./... &&
  GOOS=windows go vet ./... &&
  GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags='-s -w -H windowsgui -X main.version=${VERSION}' -o dist/cabine-agent.exe ./cmd/cabine-agent"
cp instalar.cmd dist/instalar.cmd
(cd dist && sha256sum cabine-agent.exe > cabine-agent.exe.sha256)
ls -la dist
