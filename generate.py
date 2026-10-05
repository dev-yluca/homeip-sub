#!/usr/bin/env python3
"""从 VPN Gate 拉取家宽节点，生成 mihomo 可用的 jiakuan.yaml。
输出格式与 Cloudflare Worker 的 ?target=vg 保持一致。
"""
import base64
import csv
import re
import sys
import urllib.request

UUID = "fcd5b2fb-2a7b-4536-bfb9-288a8411d9d3"
DOMAIN = "jiakuan.moreyu-09.workers.dev"
VPNGATE_API = "https://www.vpngate.net/api/iphone/"
MAX_NODES = 80  # 最多取多少个节点

HEADER = """# 家宽订阅：CF 节点带路，落地是住宅宽带
# 内核要 1.19.25 以上，老内核不认这类节点
# 节点是网友共享的，掉线很正常，家宽自动会自己往下换
# 由 GitHub Actions 每小时自动更新
mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
ipv6: false
unified-delay: true
tcp-concurrent: true
external-controller: 127.0.0.1:9090
dns:
  enable: true
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  nameserver:
    - https://223.5.5.5/dns-query
    - https://1.1.1.1/dns-query

proxies:
  - name: "%s:443"
    type: vless
    server: "%s"
    port: 443
    uuid: %s
    udp: true
    tls: true
    client-fingerprint: chrome
    servername: "%s"
    network: ws
    ws-opts:
      path: "/?ed=2048"
      headers:
        Host: "%s"
""" % (DOMAIN, DOMAIN, UUID, DOMAIN, DOMAIN)


def fetch_vpngate():
    req = urllib.request.Request(VPNGATE_API, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


def parse_ovpn(b64):
    """解析 base64 的 OpenVPN 配置，返回 dict。"""
    try:
        text = base64.b64decode(b64).decode("utf-8", errors="ignore")
    except Exception:
        return None
    info = {}
    m = re.search(r"^remote\s+(\S+)\s+(\d+)", text, re.M)
    if not m:
        return None
    info["server"] = m.group(1)
    info["port"] = int(m.group(2))
    m = re.search(r"^proto\s+(\S+)", text, re.M)
    info["proto"] = m.group(1).lower() if m else "tcp"
    # 只取 TCP 的，mihomo openvpn 走 tcp 更稳
    if "udp" in info["proto"]:
        # 尝试找 tcp 的 remote
        m2 = re.search(r"^remote\s+(\S+)\s+(\d+).*tcp", text, re.M | re.I)
        # 简单起见：udp 的跳过
        return None
    m = re.search(r"^cipher\s+(\S+)", text, re.M)
    info["cipher"] = m.group(1) if m else "AES-128-CBC"
    m = re.search(r"^auth\s+(\S+)", text, re.M)
    info["auth"] = m.group(1) if m else "SHA1"
    m = re.search(r"<ca>(.*?)</ca>", text, re.S)
    if not m:
        return None
    info["ca"] = m.group(1).strip()
    return info


def main():
    print("Fetching VPN Gate...", file=sys.stderr)
    data = fetch_vpngate()
    lines = data.strip().split("\n")
    # 去掉开头注释行和结尾 *
    rows = [l for l in lines if not l.startswith("#") and not l.startswith("*")]
    reader = csv.reader(rows)
    header = next(reader)
    idx = {name: i for i, name in enumerate(header)}

    nodes = []
    country_count = {}
    for row in reader:
        try:
            if len(row) < len(header):
                continue
            country = row[idx["CountryShort"]]
            ip = row[idx["IP"]]
            b64 = row[idx["OpenVPN_ConfigData_Base64"]]
            if not b64:
                continue
            ovpn = parse_ovpn(b64)
            if not ovpn:
                continue
            # 按国家计数命名
            country_count[country] = country_count.get(country, 0) + 1
            name = "🏠 %s-家宽-%02d" % (country, country_count[country])
            nodes.append((name, ovpn))
            if len(nodes) >= MAX_NODES:
                break
        except Exception as e:
            print("skip row: %s" % e, file=sys.stderr)
            continue

    print("Got %d nodes" % len(nodes), file=sys.stderr)

    out = [HEADER]
    for name, ovpn in nodes:
        # ca 用 YAML block scalar，缩进处理
        ca_lines = "\n".join("      " + l for l in ovpn["ca"].split("\n"))
        out.append('''  - name: "%s"
    type: openvpn
    server: %s
    port: %d
    proto: tcp
    username: vpn
    password: vpn
    cipher: %s
    auth: %s
    udp: false
    handshake-timeout: 30
    remote-dns-resolve: true
    dns: [ 8.8.8.8, 1.1.1.1 ]
    dialer-proxy: "⚡ CF前置"
    ca: &jkca |-
%s
''' % (name, ovpn["server"], ovpn["port"], ovpn["cipher"], ovpn["auth"], ca_lines))

    # proxy-groups
    out.append('proxy-groups:')
    out.append('''  - name: "⚡ CF前置"
    type: url-test
    url: https://www.gstatic.com/generate_204
    interval: 300
    tolerance: 50
    proxies:
      - "%s:443"
''' % DOMAIN)
    out.append('''  - name: "🏠 家宽自动"
    type: fallback
    url: https://www.gstatic.com/generate_204
    interval: 1800
    lazy: true
    proxies:
''')
    for name, _ in nodes:
        out.append('      - "%s"\n' % name)

    # rules
    out.append('''rules:
  - MATCH,🏠 家宽自动
''')

    sys.stdout.write("".join(out))


if __name__ == "__main__":
    main()
