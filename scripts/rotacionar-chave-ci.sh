#!/usr/bin/env bash
# Cria (ou troca) a chave SSH que o GitHub Actions usa para fazer deploy na VPS.
#
#   ./scripts/rotacionar-chave-ci.sh            # gera chave nova, instala na VPS, atualiza os secrets e remove a antiga
#   VPS=root@1.2.3.4 REPO=dono/repo ./scripts/rotacionar-chave-ci.sh
#
# Requisitos: acesso SSH a VPS (sua chave) e `gh auth login` com permissao de escrever secrets no repositorio.
# Seguranca: a chave privada fica so numa pasta temporaria (apagada ao final) e vai direto para o secret do GitHub;
# a chave nova e testada ANTES de remover a antiga, e o authorized_keys da VPS ganha um backup (~/.ssh/authorized_keys.bak-ci).
set -euo pipefail

VPS="${VPS:-root@95.111.250.4}"
REPO="${REPO:-icarosimoes/cabine}"
TAG="github-actions-deploy-cabine"   # comentario que identifica a chave deste projeto no authorized_keys

T="$(mktemp -d)"
trap 'shred -u "$T"/* 2>/dev/null || rm -f "$T"/*; rmdir "$T" 2>/dev/null || true' EXIT

echo "1/5 gerando chave nova ($TAG)"
ssh-keygen -q -t ed25519 -N "" -C "$TAG" -f "$T/k"

echo "2/5 instalando a chave nova na VPS (a antiga continua valendo por enquanto)"
ssh "$VPS" 'cp -p ~/.ssh/authorized_keys ~/.ssh/authorized_keys.bak-ci && cat >> ~/.ssh/authorized_keys' < "$T/k.pub"

echo "3/5 testando o acesso com a chave nova"
ssh -i "$T/k" -o IdentitiesOnly=yes -o BatchMode=yes "$VPS" 'echo "   ok: $(hostname)"'

echo "4/5 atualizando os secrets VPS_SSH_KEY e VPS_HOST no GitHub ($REPO)"
gh secret set VPS_SSH_KEY -R "$REPO" < "$T/k"
gh secret set VPS_HOST -R "$REPO" --body "${VPS#*@}"

echo "5/5 removendo chaves antigas deste projeto da VPS (mantendo so a nova)"
ssh "$VPS" "set -e
  grep -v ' $TAG\$' ~/.ssh/authorized_keys > /tmp/ak.new || true   # tira as antigas (so as deste projeto)
  cat >> /tmp/ak.new                                                 # poe a nova no fim
  [ \"\$(wc -l < /tmp/ak.new)\" -ge 2 ] || { echo 'abortado: authorized_keys ficaria vazio'; exit 1; }
  install -m 600 /tmp/ak.new ~/.ssh/authorized_keys && rm -f /tmp/ak.new
  echo \"   authorized_keys: \$(wc -l < ~/.ssh/authorized_keys) chaves, \$(grep -c ' $TAG\$' ~/.ssh/authorized_keys) deste projeto\"" < "$T/k.pub"

echo "pronto. Valide com:  gh workflow run ci -R $REPO   (acompanhe: gh run watch -R $REPO)"
