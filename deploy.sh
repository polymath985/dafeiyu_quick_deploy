#!/usr/bin/env bash
# Fresh Ubuntu 26.04 deployment. No QQ password or automatic QQ login.
set -Eeuo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [[ $EUID != 0 ]]; then exec sudo bash "$0" "$@"; fi
umask 077
export DEBIAN_FRONTEND=noninteractive
mkdir -p state
chmod 700 state
trap 'printf "\n部署在第 %s 行停止；修正原因后重跑 sudo bash deploy.sh。\n" "$LINENO" >&2' ERR

if [[ ${1:-} == --check ]]; then
  python3 configure.py check
  exit
fi
if [[ ${1:-} == --configure ]]; then
  rm -f state/configured.sha256 state/voice-failed.sha256
  python3 configure.py apply
  python3 configure.py check
  exit
fi
if [[ -n ${1:-} ]]; then echo '用法：sudo bash deploy.sh [--check|--configure]' >&2; exit 2; fi
test -f key.txt || { echo '请先把 key.example.txt 复制为 key.txt 并填写。' >&2; exit 1; }
chmod 600 key.txt
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 26.04 ]] || { echo '本脚本面向 Ubuntu 26.04 LTS。' >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo '已固定验证的镜像为 amd64，请使用 x86_64 服务器。' >&2; exit 1; }

echo '[1/5] 配置国内软件源并安装 Docker、Compose、Python'
if ! test -f state/apt-configured; then
  mkdir -p state/apt-original
  cp -a /etc/apt/sources.list.d/ubuntu.sources state/apt-original/ 2>/dev/null || true
  cp -a /etc/apt/sources.list state/apt-original/ 2>/dev/null || true
  for file in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
    if test -f "$file"; then
      sed -i -E 's#https?://([a-zA-Z0-9.-]+\.)?(archive|security)\.ubuntu\.com/ubuntu/?#https://mirrors.aliyun.com/ubuntu/#g; s#https?://(mirrors|mirrors\.cloud)\.aliyuncs\.com/ubuntu/?#https://mirrors.aliyun.com/ubuntu/#g' "$file"
    fi
  done
  touch state/apt-configured
fi
apt-get -o Acquire::Retries=3 -o DPkg::Lock::Timeout=120 update
apt-get -o Acquire::Retries=3 -o DPkg::Lock::Timeout=120 install -y ca-certificates curl python3 tar
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl --fail --location --retry 3 --connect-timeout 15 --max-time 120 \
    https://mirror.nju.edu.cn/docker-ce/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://mirror.nju.edu.cn/docker-ce/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get -o Acquire::Retries=3 -o DPkg::Lock::Timeout=120 update
  apt-get -o Acquire::Retries=3 -o DPkg::Lock::Timeout=120 install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
if [[ $(awk '/MemTotal/{print $2}' /proc/meminfo) -lt 2100000 ]] && \
   [[ $(awk '/SwapTotal/{print $2}' /proc/meminfo) == 0 ]] && ! test -e /swapfile; then
  if [[ $(df -Pk . | awk 'NR==2{print $4}') -gt 4194304 ]]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
    echo '小内存服务器已增加 2 GiB swap。'
  fi
fi

echo '[2/5] 生成独立配置与持久化设备标识'
python3 configure.py init
chown -R 1000:1000 napcat
chmod 644 napcat/machine-id
chmod 755 data

echo '[3/5] 下载固定版本镜像（国内镜像失败后尝试官方源）'
pull_image() {
  local target=$1 repository=$2 digest=$3 registry
  if docker image inspect "$target" >/dev/null 2>&1; then return; fi
  for registry in docker.m.daocloud.io m.daocloud.io/docker.io docker.io; do
    echo "下载 ${repository}：${registry}"
    if timeout 900 docker pull "${registry}/${repository}@${digest}"; then
      docker tag "${registry}/${repository}@${digest}" "$target"
      return
    fi
    echo '当前下载源失败，尝试下一源。'
  done
  echo "镜像 ${repository} 下载失败；检查网络后重跑，已下载的层会复用。" >&2
  return 1
}
pull_image dafeiyu-video/astrbot:4.29.3 soulter/astrbot sha256:d6f6508af78b9d0e427c0959a2fdd1016411720aad67cfcde8719870a89ee832
pull_image dafeiyu-video/napcat:4.18.41 mlikiowa/napcat-docker sha256:69c5ca84ec226d658d0d5cd19997c6c89169da3206faa2948c2dc67d669b6406

echo '[4/5] 启动 AstrBot、NapCat 并回填实际 Token'
docker compose config --quiet
docker compose up -d --pull never
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
  ufw allow 6099/tcp >/dev/null
  ufw allow 6185/tcp >/dev/null
fi
python3 configure.py sync

echo '[5/5] 导入人格、插件及模型配置'
python3 configure.py apply
python3 configure.py check
docker compose ps
printf '\n完成。请在阿里云安全组放行 TCP 6099、6185，然后用机器人 QQ 自行扫码。\n'
printf '从 Windows 一键脚本运行时，接下来会自动将 Token 同步回本机 key.txt。\n'
